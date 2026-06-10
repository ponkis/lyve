import os
import time
import json
import hashlib
import queue as pyqueue
import threading
from datetime import datetime, timezone
import librosa

from lyve.config import Config
from lyve.services.metadata import extract_metadata_and_bpm
from lyve.services.lyrics import fetch_lyrics_from_lrclib, parse_lrc, transcribe_local
from lyve.services.audio import fetch_bpm_from_deezer, detect_bpm

processing_queue = pyqueue.Queue()
pending_jobs = []
pending_lock = threading.Lock()

def compute_file_hash_bytes(file_bytes):
    """Compute SHA256 hash for raw file bytes."""
    h = hashlib.sha256()
    h.update(file_bytes)
    return h.hexdigest()


def ponk_path_for_hash(file_hash):
    return os.path.join(Config.CACHE_FOLDER, f"{file_hash}.ponk")


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

            for name in os.listdir(Config.UPLOAD_FOLDER):
                path = os.path.join(Config.UPLOAD_FOLDER, name)
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

                        progress_path = os.path.join(Config.CACHE_FOLDER, f"{h}.progress.json")
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
                                        "fetching_lyrics",
                                        "ai_transcription"
                                    ):
                                        continue
                            except Exception:
                                pass

                    mtime = os.path.getmtime(path)
                    if now - mtime > Config.UPLOAD_TTL_SECONDS:
                        os.remove(path)

                except Exception as e:
                    print(f"Error cleaning upload {path}: {e}")

            cutoff = now - (Config.CACHE_TTL_DAYS * 24 * 3600)
            for name in os.listdir(Config.CACHE_FOLDER):
                path = os.path.join(Config.CACHE_FOLDER, name)
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


def queue_dispatcher():
    while True:
        try:
            item = processing_queue.get()
            if not item:
                processing_queue.task_done()
                continue
            filepath, file_hash, filename, is_ponk_validation, original_filename = item

            with pending_lock:
                try:
                    if file_hash in pending_jobs:
                        pending_jobs.remove(file_hash)
                except Exception:
                    pass

            progress_path = os.path.join(Config.CACHE_FOLDER, f"{file_hash}.progress.json")
            try:
                with open(progress_path, "w", encoding="utf-8") as pf:
                    json.dump(
                        {
                            "progress": 1,
                            "status": "starting",
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        },
                        pf,
                    )
            except Exception:
                pass

            try:
                process_file_async(filepath, file_hash, filename, is_ponk_validation, original_filename)
            except Exception as e:
                print(f"Dispatcher failed processing {file_hash}: {e}")

            processing_queue.task_done()
        except Exception as e:
            print(f"Queue dispatcher error: {e}")
            time.sleep(1)


def process_file_async(filepath, file_hash, filename, is_ponk_validation=False, original_filename=""):
    try:
        if load_cached_ponk(file_hash) is not None:
            return
    except Exception:
        pass

    words = []
    lyrics = ""
    lyrics_source = ""
    bpm = None
    duration = None

    progress_path = os.path.join(Config.CACHE_FOLDER, f"{file_hash}.progress.json")

    def write_progress(pct, status_text="processing"):
        try:
            with open(progress_path, "w", encoding="utf-8") as pf:
                json.dump(
                    {
                        "progress": int(pct),
                        "status": status_text,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    pf,
                )
        except Exception as e:
            print(f"Failed to write progress for {file_hash}: {e}")

    write_progress(1, "starting")

    # Get duration early for API matching
    try:
        duration = librosa.get_duration(path=filepath)
    except Exception as e:
        print(f"Error getting duration early: {e}")

    # Extract tags & parse filename
    artist, title, album, tag_bpm = extract_metadata_and_bpm(filepath, original_filename or filename)
    bpm = tag_bpm

    if not is_ponk_validation:
        # 1. Try to fetch lyrics from LRCLIB
        if artist and title:
            write_progress(10, "fetching_lyrics")
            lrclib_data = fetch_lyrics_from_lrclib(artist, title, album, duration)
            if lrclib_data:
                if lrclib_data.get("instrumental") is True:
                    lyrics = "[Instrumental]"
                    words = []
                    lyrics_source = "lrclib"
                elif lrclib_data.get("syncedLyrics"):
                    synced_text = lrclib_data["syncedLyrics"]
                    words = parse_lrc(synced_text, duration)
                    if words:
                        lyrics = lrclib_data.get("plainLyrics", "")
                        if not lyrics:
                            lyrics = " ".join([w["word"] for w in words])
                        lyrics_source = "lrclib"
                
                # If synced lyrics were empty/failed, try plain lyrics
                if not lyrics_source and lrclib_data.get("plainLyrics"):
                    plain_text = lrclib_data["plainLyrics"]
                    lyrics = plain_text
                    words = plain_text.split()
                    lyrics_source = "lrclib_unsynced"

        # 2. Fallback to AI (Whisper) transcription if LRCLIB failed/not found
        if not lyrics_source:
            write_progress(20, "ai_transcription")
            try:
                words, lyrics = transcribe_local(filepath, duration)
                if words:
                    lyrics_source = "ai"
                else:
                    raise Exception("Local transcription failed")
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
        # Determine BPM: Mutagen tags -> Deezer API -> Librosa local estimation
        if bpm is None and artist and title:
            bpm = fetch_bpm_from_deezer(artist, title)
            
        if bpm is None:
            bpm = detect_bpm(filepath)

        # Get final duration if we haven't already
        if duration is None:
            try:
                duration = librosa.get_duration(path=filepath)
            except Exception:
                duration = None

        file_size = os.path.getsize(filepath)

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
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        save_cached_ponk(file_hash, cached_obj)

        try:
            write_progress(100, "ready")
        except Exception:
            pass
    except Exception as e:
        print(f"Background worker failed for {filepath}: {e}")
