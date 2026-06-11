from flask import Blueprint, abort, render_template, request, jsonify, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
import os
from datetime import datetime, timezone

from lyve.config import Config
from lyve.services.worker import (
    processing_queue,
    pending_jobs,
    pending_lock,
    load_cached_ponk,
    compute_file_hash_bytes
)
from lyve.services.upload_validation import (
    UploadValidationError,
    is_safe_upload_filename,
    validate_and_store_audio_upload,
)

lyve_bp = Blueprint(
    "lyve", 
    __name__, 
    template_folder="../templates", 
    static_folder="../static"
)


@lyve_bp.route("/")
def index():
    return render_template("index.html")


@lyve_bp.route("/.well-known/discord")
def discord_verification():
    return ("dh=8a3f138becf048a47ced20b11bcbf8be9cb6c894", 200, {"Content-Type": "text/plain"})


@lyve_bp.route("/upload", methods=["POST"])
def upload_file():
    if Config.DEBUG_UPLOAD_LOGS:
        try:
            print("=== /upload request ===")
            print("remote_addr=", request.remote_addr)
            print("host=", request.host)
            print("url=", request.url)
            for h in ["Host", "Content-Type", "Content-Length", "X-Forwarded-For", "Referer", "User-Agent"]:
                print(f"{h}: {request.headers.get(h)}")
            print("======================")
        except Exception as _e:
            print("Failed to log request headers:", _e)

    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    upload_limit = Config.MAX_CONTENT_LENGTH or (50 * 1024 * 1024)
    file_bytes = file.stream.read(upload_limit + 1)
    if len(file_bytes) > upload_limit:
        return jsonify({"error": "File too large", "status": 413}), 413

    try:
        validated = validate_and_store_audio_upload(file, file_bytes)
    except UploadValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Failed to validate uploaded file: {e}")
        return jsonify({"error": "Failed to validate uploaded audio"}), 500

    file_hash = validated.file_hash
    filename = validated.filename
    filepath = validated.filepath

    try:
        print(
            f"{datetime.now(timezone.utc).isoformat()} ACCEPTED /upload stored={filename} client={request.remote_addr}"
        )
    except Exception:
        pass

    cached = load_cached_ponk(file_hash)
    if cached is not None:
        resp = cached.copy()
        resp["from_cache"] = True
        resp["filename"] = filename
        return jsonify(resp)

    is_ponk_validation = request.form.get("is_ponk_validation", "false") == "true"

    try:
        progress_path = os.path.join(Config.CACHE_FOLDER, f"{file_hash}.progress.json")
        try:
            with open(progress_path, "w", encoding="utf-8") as pf:
                json_dump_data = {
                    "progress": 0,
                    "status": "queued",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                import json
                json.dump(json_dump_data, pf)
        except Exception as _e:
            print(f"Failed to write initial progress for {file_hash}: {_e}")

        with pending_lock:
            if file_hash not in pending_jobs:
                pending_jobs.append(file_hash)
            queue_position = pending_jobs.index(file_hash) + 1

        processing_queue.put(
            (filepath, file_hash, filename, is_ponk_validation, validated.original_filename)
        )
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


@lyve_bp.route("/uploads/<filename>")
def uploaded_file(filename):
    if not is_safe_upload_filename(filename):
        abort(404)
    response = send_from_directory(Config.UPLOAD_FOLDER, filename, max_age=Config.UPLOAD_TTL_SECONDS)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@lyve_bp.route("/status/<file_hash>")
def status(file_hash):
    if not file_hash.isalnum() or len(file_hash) != 64:
        return jsonify({"error": "Invalid hash format"}), 400

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

    progress_path = os.path.join(Config.CACHE_FOLDER, f"{file_hash}.progress.json")
    if os.path.exists(progress_path):
        try:
            with open(progress_path, "r", encoding="utf-8") as pf:
                import json
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

    for name in os.listdir(Config.UPLOAD_FOLDER):
        path = os.path.join(Config.UPLOAD_FOLDER, name)
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


@lyve_bp.route("/result/<file_hash>")
def result(file_hash):
    if not file_hash.isalnum() or len(file_hash) != 64:
        return jsonify({"error": "Invalid hash format"}), 400

    cached = load_cached_ponk(file_hash)
    if cached is None:
        return jsonify({"status": "processing_or_missing"}), 404
    resp = cached.copy()
    resp["from_cache"] = True
    return jsonify(resp)


@lyve_bp.app_errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return jsonify({"error": "File too large", "status": 413}), 413
