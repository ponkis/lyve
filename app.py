from flask import Flask, render_template, request, jsonify
from werkzeug.exceptions import RequestEntityTooLarge
from flask_cors import CORS
import os
from werkzeug.utils import secure_filename
import librosa
import mutagen
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
import random
import string
from faster_whisper import WhisperModel
import hashlib
import json
import threading
import queue as pyqueue
import time
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

model_size = "base"

model = WhisperModel(model_size, device="cpu", compute_type="int8")

UPLOAD_FOLDER = "uploads"
CACHE_FOLDER = "cache"
ALLOWED_EXTENSIONS = {"mp3", "wav", "flac", "ogg", "m4a"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CACHE_FOLDER, exist_ok=True)

UPLOAD_TTL_SECONDS = 5 * 60
CACHE_TTL_DAYS = 7


def compute_file_hash_bytes(file_bytes):
    """Compute SHA256 hash for raw file bytes."""
    h = hashlib.sha256()
    h.update(file_bytes)
    return h.hexdigest()


def ponk_path_for_hash(file_hash):
    return os.path.join(CACHE_FOLDER, f"{file_hash}.ponk")


def load_cached_ponk(file_hash):
    path = ponk_path_for_hash(file_hash)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Failed to load .ponk file {path}: {e}")
    return None


def save_cached_ponk(file_hash, data):
    path = ponk_path_for_hash(file_hash)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to save .ponk file {path}: {e}")


def cleanup_worker():
    while True:
        try:
            now = time.time()

            for name in os.listdir(UPLOAD_FOLDER):
                path = os.path.join(UPLOAD_FOLDER, name)
                try:
                    if not os.path.isfile(path):
                        continue

                    try:
                        with open(path, "rb") as f:
                            data = f.read()
                        h = compute_file_hash_bytes(data)
                    except Exception:
                        h = None

                    if h:

                        with pending_lock:
                            if h in pending_jobs:
                                continue

                        progress_path = os.path.join(CACHE_FOLDER, f"{h}.progress.json")
                        if os.path.exists(progress_path):
                            try:
                                with open(progress_path, "r", encoding="utf-8") as pf:
                                    pj = json.load(pf)
                                    if pj.get("status") in (
                                        "queued",
                                        "transcribing",
                                        "starting",
                                        "finalizing",
                                        "processing",
                                    ):
                                        continue
                            except Exception:
                                pass

                    mtime = os.path.getmtime(path)
                    if now - mtime > UPLOAD_TTL_SECONDS:
                        os.remove(path)

                except Exception as e:
                    print(f"Error cleaning upload {path}: {e}")

            cutoff = now - (CACHE_TTL_DAYS * 24 * 3600)
            for name in os.listdir(CACHE_FOLDER):
                path = os.path.join(CACHE_FOLDER, name)
                try:
                    if not os.path.isfile(path):
                        continue
                    mtime = os.path.getmtime(path)
                    if mtime < cutoff:
                        os.remove(path)

                except Exception as e:
                    print(f"Error cleaning cache {path}: {e}")

        except Exception as e:
            print(f"Cleanup thread error: {e}")

        time.sleep(60)


try:
    cleaner = threading.Thread(target=cleanup_worker, daemon=True)
    cleaner.start()
except Exception as e:
    print(f"Failed to start cleanup thread: {e}")

processing_queue = pyqueue.Queue()
pending_jobs = []
pending_lock = threading.Lock()


def queue_dispatcher():
    while True:
        try:
            item = processing_queue.get()
            if not item:
                processing_queue.task_done()
                continue
            filepath, file_hash, filename, is_ponk_validation = item

            with pending_lock:
                try:
                    if file_hash in pending_jobs:
                        pending_jobs.remove(file_hash)
                except Exception:
                    pass

            progress_path = os.path.join(CACHE_FOLDER, f"{file_hash}.progress.json")
            try:
                with open(progress_path, "w", encoding="utf-8") as pf:
                    json.dump(
                        {
                            "progress": 1,
                            "status": "starting",
                            "updated_at": datetime.utcnow().isoformat(),
                        },
                        pf,
                    )
            except Exception:
                pass

            try:
                process_file_async(filepath, file_hash, filename, is_ponk_validation)
            except Exception as e:
                print(f"Dispatcher failed processing {file_hash}: {e}")

            processing_queue.task_done()
        except Exception as e:
            print(f"Queue dispatcher error: {e}")
            time.sleep(1)


try:
    dispatcher = threading.Thread(target=queue_dispatcher, daemon=True)
    dispatcher.start()
except Exception as e:
    print(f"Failed to start queue dispatcher: {e}")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def detect_bpm(file_path):
    try:

        y, sr = librosa.load(file_path, duration=60)

        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        tempo = librosa.feature.tempo(onset_envelope=onset_env, sr=sr)

        if tempo is not None and len(tempo) > 0:
            return float(tempo[0])

        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        return float(tempo)

    except AttributeError as e:

        print(f"AttributeError in BPM detection (scipy issue): {e}")
        try:

            y, sr = librosa.load(file_path, duration=30)
            tempo = librosa.feature.tempo(y=y, sr=sr)
            if tempo is not None and len(tempo) > 0:
                return float(tempo[0])
        except Exception:
            pass
        return 120.0

    except Exception as e:
        print(f"Error detecting BPM: {e}")
        return 120.0


def transcribe_with_local_whisper(file_path):
    try:
        segments, info = model.transcribe(file_path, word_timestamps=True)

        timed_words = []
        full_lyrics = ""

        for segment in segments:
            full_lyrics += segment.text + " "
            for word in segment.words:
                timed_words.append(
                    {"word": word.word, "start": word.start, "end": word.end}
                )

        return timed_words, full_lyrics.strip()

    except Exception as e:
        print(f"Error with local Whisper transcription: {e}")
        return None, None


def process_file_async(filepath, file_hash, filename, is_ponk_validation=False):
    try:

        if load_cached_ponk(file_hash) is not None:
            return
    except Exception:
        pass

    words = []
    lyrics = ""
    lyrics_source = "ai"

    progress_path = os.path.join(CACHE_FOLDER, f"{file_hash}.progress.json")

    def write_progress(pct, status_text="processing"):
        try:
            with open(progress_path, "w", encoding="utf-8") as pf:
                json.dump(
                    {
                        "progress": int(pct),
                        "status": status_text,
                        "updated_at": datetime.utcnow().isoformat(),
                    },
                    pf,
                )
        except Exception as e:
            print(f"Failed to write progress for {file_hash}: {e}")

    write_progress(1, "starting")

    if not is_ponk_validation:

        try:
            segments, info = model.transcribe(filepath, word_timestamps=True)
            total_duration = (
                info.duration if hasattr(info, "duration") and info.duration else None
            )
            if total_duration is None:

                try:
                    y_tmp, sr_tmp = librosa.load(filepath, duration=1)
                    total_duration = librosa.get_duration(filename=filepath)
                except Exception:
                    total_duration = None

            full_lyrics = ""
            timed_words = []
            last_end = 0.0
            for i, segment in enumerate(segments):
                full_lyrics += segment.text + " "
                for word in getattr(segment, "words", []):
                    timed_words.append(
                        {"word": word.word, "start": word.start, "end": word.end}
                    )
                    last_end = max(last_end, word.end)

                if total_duration and last_end:
                    pct = min(99, int((last_end / total_duration) * 100))
                else:

                    pct = min(99, int(((i + 1) / max(1, len(segments))) * 100))
                write_progress(pct, "transcribing")

            words = timed_words
            lyrics = full_lyrics.strip()
        except Exception as e:
            print(f"Error with local Whisper transcription: {e}")
            lyrics = "AI transcription failed. Please try another file."
            words = [
                {"word": "AI", "start": 0, "end": 1},
                {"word": "failed.", "start": 1, "end": 2},
            ]
            lyrics_source = "error"

    write_progress(95, "finalizing")

    try:

        try:
            y, sr = librosa.load(filepath)
            duration = librosa.get_duration(y=y, sr=sr)
        except Exception:
            duration = None
        file_size = os.path.getsize(filepath)
        bpm = detect_bpm(filepath)

        response_payload = {
            "success": True,
            "lyrics": lyrics,
            "words": words,
            "bpm": bpm,
            "lyrics_source": lyrics_source,
            "metadata": {
                "originalFilename": filename,
                "fileSize": file_size,
                "duration": duration,
            },
        }

        cached_obj = response_payload.copy()

        cached_obj["filename"] = filename
        cached_obj["_meta"] = {
            "originalFilename": filename,
            "cached_at": datetime.utcnow().isoformat(),
        }
        save_cached_ponk(file_hash, cached_obj)

        try:
            write_progress(100, "ready")
        except Exception:
            pass
    except Exception as e:
        print(f"Background worker failed for {filepath}: {e}")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/.well-known/discord")
def discord_verification():
    return ("dh=8a3f138becf048a47ced20b11bcbf8be9cb6c894", 200, {"Content-Type": "text/plain"})


@app.route("/upload", methods=["POST"])
def upload_file():

    try:
        print("=== /upload request ===")
        print("remote_addr=", request.remote_addr)
        print("host=", request.host)
        print("url=", request.url)
        for h in [
            "Host",
            "Content-Type",
            "Content-Length",
            "X-Forwarded-For",
            "Referer",
            "User-Agent",
        ]:
            print(f"{h}: {request.headers.get(h)}")
        print("======================")
    except Exception as _e:
        print("Failed to log request headers:", _e)

    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    if file and allowed_file(file.filename):

        try:
            print(
                f"{datetime.utcnow().isoformat()} RECEIVED /upload for filename={file.filename} client={request.remote_addr}"
            )
        except Exception:
            pass
        file_bytes = file.read()
        file_hash = compute_file_hash_bytes(file_bytes)

        cached = load_cached_ponk(file_hash)
        if cached is not None:

            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            try:

                with open(filepath, "wb") as f:
                    f.write(file_bytes)
            except Exception as e:
                print(f"Failed to write temp upload file: {e}")

            resp = cached.copy()
            resp["from_cache"] = True
            resp["filename"] = filename
            return jsonify(resp)

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        try:
            with open(filepath, "wb") as f:
                f.write(file_bytes)
        except Exception as e:
            print(f"Failed to save uploaded file: {e}")
            return jsonify({"error": "Failed to save file"}), 500

        is_ponk_validation = request.form.get("is_ponk_validation", "false") == "true"

        try:

            progress_path = os.path.join(CACHE_FOLDER, f"{file_hash}.progress.json")
            try:
                with open(progress_path, "w", encoding="utf-8") as pf:
                    json.dump(
                        {
                            "progress": 0,
                            "status": "queued",
                            "updated_at": datetime.utcnow().isoformat(),
                        },
                        pf,
                    )
            except Exception as _e:
                print(f"Failed to write initial progress for {file_hash}: {_e}")

            with pending_lock:
                pending_jobs.append(file_hash)
                queue_position = len(pending_jobs)

            processing_queue.put((filepath, file_hash, filename, is_ponk_validation))
        except Exception as e:
            print(f"Failed to enqueue background worker: {e}")
            return jsonify({"error": "Failed to start processing"}), 500

        try:
            print(
                f"{datetime.utcnow().isoformat()} RETURNING 202 for file_hash={file_hash} filename={filename}"
            )
        except Exception:
            pass
    return (
        jsonify(
            {
                "success": True,
                "status": "queued",
                "file_hash": file_hash,
                "filename": filename,
                "queue_position": queue_position,
            }
        ),
        202,
    )

    return jsonify({"error": "Invalid file type"}), 400


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    from flask import send_from_directory

    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/status/<file_hash>")
def status(file_hash):

    cached = load_cached_ponk(file_hash)
    if cached is not None:
        return jsonify(
            {
                "status": "ready",
                "from_cache": True,
                "file_hash": file_hash,
                "progress": 100,
            }
        )

    progress_path = os.path.join(CACHE_FOLDER, f"{file_hash}.progress.json")
    if os.path.exists(progress_path):
        try:
            with open(progress_path, "r", encoding="utf-8") as pf:
                pj = json.load(pf)
                resp = {
                    "status": pj.get("status", "processing"),
                    "progress": int(pj.get("progress", 0)),
                    "file_hash": file_hash,
                }

                if resp["status"] == "queued":
                    with pending_lock:
                        try:
                            pos = (
                                pending_jobs.index(file_hash) + 1
                                if file_hash in pending_jobs
                                else None
                            )
                        except Exception:
                            pos = None
                    if pos:
                        resp["queue_position"] = pos
                return jsonify(resp)
        except Exception as e:
            print(f"Failed to read progress for {file_hash}: {e}")
            return jsonify(
                {"status": "processing", "progress": 0, "file_hash": file_hash}
            )

    for name in os.listdir(UPLOAD_FOLDER):
        path = os.path.join(UPLOAD_FOLDER, name)
        try:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except Exception:
                continue
            if compute_file_hash_bytes(data) == file_hash:

                return jsonify(
                    {"status": "processing", "progress": 0, "file_hash": file_hash}
                )
        except Exception:
            continue

    return jsonify({"status": "not_found", "file_hash": file_hash}), 404


@app.route("/result/<file_hash>")
def result(file_hash):
    cached = load_cached_ponk(file_hash)
    if cached is None:
        return jsonify({"status": "processing_or_missing"}), 404
    resp = cached.copy()
    resp["from_cache"] = True
    return jsonify(resp)


if __name__ == "__main__":

    app.run(debug=False, host="0.0.0.0", port=5500, threaded=True)


@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):

    return jsonify({"error": "File too large", "status": 413}), 413