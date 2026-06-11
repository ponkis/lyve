import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Optional

import mutagen
from werkzeug.utils import secure_filename

from lyve.config import Config


SAFE_UPLOAD_RE = re.compile(r"^[a-f0-9]{64}\.(?:mp3|wav|flac|ogg|m4a)$")


class UploadValidationError(ValueError):
    """Raised when an uploaded file fails audio validation."""


@dataclass(frozen=True)
class ValidatedAudioUpload:
    file_hash: str
    filename: str
    filepath: str
    original_filename: str
    extension: str
    content_type: str
    file_size: int
    duration: Optional[float]


def compute_file_hash_bytes(file_bytes):
    h = hashlib.sha256()
    h.update(file_bytes)
    return h.hexdigest()


def is_safe_upload_filename(filename):
    return bool(SAFE_UPLOAD_RE.fullmatch(filename or ""))


def _extension_for(filename):
    safe_name = secure_filename(filename or "")
    if not safe_name or "." not in safe_name:
        raise UploadValidationError("Upload must include a supported audio filename.")

    extension = safe_name.rsplit(".", 1)[1].lower()
    if extension not in Config.ALLOWED_AUDIO_EXTENSIONS:
        allowed = ", ".join(sorted(Config.ALLOWED_AUDIO_EXTENSIONS))
        raise UploadValidationError(f"Unsupported file extension. Upload one of: {allowed}.")

    return extension


def _normalized_content_type(file_storage):
    content_type = (getattr(file_storage, "mimetype", None) or "").split(";", 1)[0]
    return content_type.strip().lower()


def _client_mime_allowed(content_type):
    if content_type in Config.GENERIC_UPLOAD_MIME_TYPES:
        return True
    return content_type in Config.ALLOWED_AUDIO_MIME_TYPES


def _has_mp3_frame_sync(header):
    return len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0


def _looks_like_audio(header, extension):
    if extension == "mp3":
        return header.startswith(b"ID3") or _has_mp3_frame_sync(header)
    if extension == "wav":
        return len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    if extension == "flac":
        return header.startswith(b"fLaC")
    if extension == "ogg":
        return header.startswith(b"OggS")
    if extension == "m4a":
        return len(header) >= 12 and header[4:8] == b"ftyp"
    return False


def _mutagen_mime_allowed(audio):
    mutagen_mimes = getattr(audio, "mime", None) or []
    if not mutagen_mimes:
        return True

    for mime in mutagen_mimes:
        mime = (mime or "").lower()
        if mime.startswith("audio/") or mime in Config.ALLOWED_AUDIO_MIME_TYPES:
            return True

    return False


def _validate_audio_metadata(path):
    try:
        audio = mutagen.File(path)
    except Exception as exc:
        raise UploadValidationError("Audio metadata could not be read.") from exc

    if audio is None or not getattr(audio, "info", None):
        raise UploadValidationError("Upload does not appear to be a supported audio file.")

    if not _mutagen_mime_allowed(audio):
        raise UploadValidationError("Upload does not appear to be an audio file.")

    duration = getattr(audio.info, "length", None)
    if duration is not None:
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = None

    if duration is not None and duration <= 0:
        raise UploadValidationError("Audio duration could not be verified.")

    max_duration = Config.MAX_AUDIO_DURATION_SECONDS
    if duration is not None and max_duration and duration > max_duration:
        minutes = max_duration // 60
        seconds = max_duration % 60
        raise UploadValidationError(
            f"Audio is too long. Maximum duration is {minutes}:{seconds:02d}."
        )

    return duration


def validate_and_store_audio_upload(file_storage, file_bytes):
    original_filename = file_storage.filename or ""
    extension = _extension_for(original_filename)
    content_type = _normalized_content_type(file_storage)

    if not _client_mime_allowed(content_type):
        raise UploadValidationError("Upload content type is not allowed for audio.")

    file_size = len(file_bytes)
    if file_size <= 0:
        raise UploadValidationError("Uploaded file is empty.")

    if Config.MAX_CONTENT_LENGTH and file_size > Config.MAX_CONTENT_LENGTH:
        raise UploadValidationError("Uploaded file is too large.")

    header = file_bytes[:64]
    if not _looks_like_audio(header, extension):
        raise UploadValidationError("Upload does not match a supported audio format.")

    file_hash = compute_file_hash_bytes(file_bytes)
    stored_filename = f"{file_hash}.{extension}"
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

    temp_path = None
    final_path = os.path.join(Config.UPLOAD_FOLDER, stored_filename)
    try:
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{file_hash}.",
            suffix=f".{extension}.tmp",
            dir=Config.UPLOAD_FOLDER,
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(file_bytes)

        duration = _validate_audio_metadata(temp_path)
        os.replace(temp_path, final_path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    return ValidatedAudioUpload(
        file_hash=file_hash,
        filename=stored_filename,
        filepath=final_path,
        original_filename=original_filename,
        extension=extension,
        content_type=content_type,
        file_size=file_size,
        duration=duration,
    )
