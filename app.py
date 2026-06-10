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
from datetime import datetime, timedelta, timezone
import re
import urllib.request
import urllib.parse

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
            filepath, file_hash, filename, is_ponk_validation, original_filename = item

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


try:
    dispatcher = threading.Thread(target=queue_dispatcher, daemon=True)
    dispatcher.start()
except Exception as e:
    print(f"Failed to start queue dispatcher: {e}")


def parse_filename_fallback(original_filename):
    """
    Extracts artist and title from filename by splitting on common separators.
    """
    if not original_filename:
        return "", ""
    base, _ = os.path.splitext(original_filename)
    # Common separators: " - ", " -", "- ", "-"
    for sep in [" - ", " -", "- ", "-"]:
        if sep in base:
            parts = base.split(sep, 1)
            artist = parts[0].strip()
            title = parts[1].strip()
            if artist and title:
                return artist, title
    return "", base.strip()


def extract_metadata_and_bpm(file_path, original_filename):
    """
    Extracts artist, title, album, and bpm tags from the file using mutagen.
    Falls back to filename parsing if artist/title are empty.
    """
    artist, title, album, bpm = "", "", "", None
    
    # Try reading tags via mutagen.File easy=True
    try:
        audio = mutagen.File(file_path, easy=True)
        if audio is not None:
            artist = audio.get("artist", [""])[0].strip()
            title = audio.get("title", [""])[0].strip()
            album = audio.get("album", [""])[0].strip()
            bpm_val = audio.get("bpm")
            if bpm_val:
                try:
                    bpm = float(bpm_val[0])
                except (ValueError, TypeError):
                    pass
            # Some easy tags map tempo to "tempo"
            if bpm is None:
                tempo_val = audio.get("tempo")
                if tempo_val:
                    try:
                        bpm = float(tempo_val[0])
                    except (ValueError, TypeError):
                        pass
    except Exception as e:
        print(f"Error reading easy mutagen tags: {e}")

    # Fallback to raw tags if EasyID3/EasyTag didn't capture bpm
    if bpm is None:
        try:
            audio_raw = mutagen.File(file_path)
            if audio_raw is not None:
                # MP3 (ID3 tags)
                if hasattr(audio_raw, "tags") and audio_raw.tags:
                    for tag in ["TBPM", "bpm", "tempo"]:
                        if tag in audio_raw.tags:
                            try:
                                val = audio_raw.tags[tag]
                                if hasattr(val, "text"):
                                    bpm = float(val.text[0])
                                else:
                                    bpm = float(val[0])
                                break
                            except (ValueError, TypeError, IndexError):
                                pass
                # MP4/M4A
                if bpm is None and hasattr(audio_raw, "get"):
                    tmpo = audio_raw.get("tmpo")
                    if tmpo:
                        try:
                            bpm = float(tmpo[0])
                        except (ValueError, TypeError):
                            pass
        except Exception as e:
            print(f"Error reading raw mutagen tags: {e}")

    # Fallback to filename if artist or title is missing
    if not artist or not title:
        f_artist, f_title = parse_filename_fallback(original_filename)
        if not artist and f_artist:
            artist = f_artist
        if not title and f_title:
            title = f_title

    # Filter out empty bpm values (e.g. 0.0)
    if bpm is not None and bpm <= 0:
        bpm = None

    return artist, title, album, bpm


def fetch_lyrics_from_lrclib(artist, title, album, duration):
    """
    Fetches lyrics from lrclib.net.
    1. Tries /api/get (strict)
    2. Tries /api/search?q=... (fuzzy) and finds the closest duration match
    Returns dict with {"syncedLyrics": ..., "plainLyrics": ..., "instrumental": ...} or None.
    """
    if not artist or not title:
        return None
        
    user_agent = "Lyve/1.0 (https://github.com/ponkis/lyve)"
    
    # 1. Try strict GET /api/get
    try:
        params = {
            "artist_name": artist,
            "track_name": title,
            "album_name": album or "",
            "duration": int(duration) if duration else 0
        }
        query_string = urllib.parse.urlencode(params)
        url = f"https://lrclib.net/api/get?{query_string}"
        
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                if data:
                    return data
    except urllib.error.HTTPError as e:
        # 404 is normal if not found, let it proceed to search
        if e.code != 404:
            print(f"LRCLIB strict lookup HTTP error: {e.code}")
    except Exception as e:
        print(f"LRCLIB strict lookup error: {e}")

    # 2. Try search GET /api/search?q=...
    try:
        query = f"{artist} {title}"
        url = f"https://lrclib.net/api/search?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status == 200:
                results = json.loads(resp.read().decode('utf-8'))
                if results and isinstance(results, list):
                    best_match = None
                    best_diff = 999999.0
                    
                    for res in results:
                        # Skip if there's no lyrics at all
                        if not res.get("syncedLyrics") and not res.get("plainLyrics"):
                            continue
                        
                        res_dur = res.get("duration")
                        if res_dur is not None and duration:
                            diff = abs(float(res_dur) - float(duration))
                        else:
                            diff = 999999.0
                            
                        # Accept if duration is within 15 seconds, or if we have no duration
                        if not duration or diff <= 15.0:
                            # Prefer synced lyrics, then select the one with smaller duration diff
                            if best_match is None:
                                best_match = res
                                best_diff = diff
                            else:
                                # Prioritize synced lyrics over unsynced
                                current_has_sync = bool(res.get("syncedLyrics"))
                                best_has_sync = bool(best_match.get("syncedLyrics"))
                                
                                if current_has_sync and not best_has_sync:
                                    best_match = res
                                    best_diff = diff
                                elif current_has_sync == best_has_sync:
                                    if diff < best_diff:
                                        best_match = res
                                        best_diff = diff
                                        
                    if best_match:
                        return best_match
    except Exception as e:
        print(f"LRCLIB search fallback error: {e}")
        
    return None


def parse_lrc(lrc_text, duration):
    """
    Parses LRC format lyrics and interpolates line-level timestamps into word-level timestamps.
    Returns a list of dicts: [{"word": word, "start": start, "end": end}, ...]
    """
    lines = []
    if not lrc_text:
        return lines

    pattern = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)]')
    
    for line in lrc_text.splitlines():
        line = line.strip()
        if not line:
            continue
            
        # Find all timestamps in this line
        matches = list(pattern.finditer(line))
        if not matches:
            continue
            
        # The text is after the last timestamp
        last_match = matches[-1]
        text = line[last_match.end():].strip()
        
        for match in matches:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            time_in_seconds = minutes * 60 + seconds
            lines.append({"time": time_in_seconds, "text": text})
            
    # Sort lines by start time
    lines.sort(key=lambda x: x["time"])
    
    # Now interpolate words
    words = []
    for i, line_data in enumerate(lines):
        line_start = line_data["time"]
        line_text = line_data["text"]
        words_in_line = line_text.split()
        if not words_in_line:
            continue
            
        # Determine when the line ends
        if i < len(lines) - 1:
            line_end = lines[i+1]["time"]
        else:
            line_end = duration if duration else (line_start + 5.0)
            
        line_duration = line_end - line_start
        if line_duration <= 0:
            line_duration = 2.0
            
        # Word timing heuristic: average words take 0.3 - 0.4 seconds, up to a max
        total_words = len(words_in_line)
        word_duration = max(0.15, min(0.4, line_duration / total_words))
        
        for j, w in enumerate(words_in_line):
            start = line_start + j * word_duration
            end = line_start + (j + 1) * word_duration
            words.append({"word": w, "start": start, "end": end})
            
    return words


def fetch_bpm_from_deezer(artist, title):
    """
    Fetches the BPM of a track from Deezer's public API.
    """
    if not artist or not title:
        return None
        
    user_agent = "Lyve/1.0 (https://github.com/ponkis/lyve)"
    
    # Try strict first, then loose
    queries = [
        f'artist:"{artist}" track:"{title}"',
        f"{artist} {title}"
    ]
    
    for query in queries:
        try:
            url = f"https://api.deezer.com/search?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    if data and "data" in data and len(data["data"]) > 0:
                        # Retrieve the track detail endpoint using the track ID of the first match
                        track_id = data["data"][0]["id"]
                        track_url = f"https://api.deezer.com/track/{track_id}"
                        
                        track_req = urllib.request.Request(track_url, headers={"User-Agent": user_agent})
                        with urllib.request.urlopen(track_req, timeout=10) as track_resp:
                            if track_resp.status == 200:
                                track_data = json.loads(track_resp.read().decode('utf-8'))
                                bpm_val = track_data.get("bpm")
                                if bpm_val:
                                    bpm_float = float(bpm_val)
                                    if bpm_float > 0:
                                        return bpm_float
        except Exception as e:
            print(f"Error fetching BPM from Deezer with query '{query}': {e}")
            
    return None


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

    progress_path = os.path.join(CACHE_FOLDER, f"{file_hash}.progress.json")

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
                segments, info = model.transcribe(filepath, word_timestamps=True)
                total_duration = (
                    info.duration if hasattr(info, "duration") and info.duration else None
                )
                if total_duration is None:
                    total_duration = duration

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
                lyrics_source = "ai"
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
                f"{datetime.now(timezone.utc).isoformat()} RECEIVED /upload for filename={file.filename} client={request.remote_addr}"
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
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        },
                        pf,
                    )
            except Exception as _e:
                print(f"Failed to write initial progress for {file_hash}: {_e}")

            with pending_lock:
                pending_jobs.append(file_hash)
                queue_position = len(pending_jobs)

            processing_queue.put((filepath, file_hash, filename, is_ponk_validation, file.filename))
        except Exception as e:
            print(f"Failed to enqueue background worker: {e}")
            return jsonify({"error": "Failed to start processing"}), 500

        try:
            print(
                f"{datetime.now(timezone.utc).isoformat()} RETURNING 202 for file_hash={file_hash} filename={filename}"
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