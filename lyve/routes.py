from flask import Blueprint, render_template, request, jsonify, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename
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

lyve_bp = Blueprint(
    "lyve", 
    __name__, 
    template_folder="../templates", 
    static_folder="../static"
)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in Config.ALLOWED_EXTENSIONS


@lyve_bp.route("/")
def index():
    return render_template("index.html")


@lyve_bp.route("/.well-known/discord")
def discord_verification():
    return ("dh=8a3f138becf048a47ced20b11bcbf8be9cb6c894", 200, {"Content-Type": "text/plain"})


@lyve_bp.route("/upload", methods=["POST"])
def upload_file():
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
            filepath = os.path.join(Config.UPLOAD_FOLDER, filename)
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
        filepath = os.path.join(Config.UPLOAD_FOLDER, filename)
        try:
            with open(filepath, "wb") as f:
                f.write(file_bytes)
        except Exception as e:
            print(f"Failed to save uploaded file: {e}")
            return jsonify({"error": "Failed to save file"}), 500

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


@lyve_bp.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(Config.UPLOAD_FOLDER, filename)


@lyve_bp.route("/status/<file_hash>")
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
    cached = load_cached_ponk(file_hash)
    if cached is None:
        return jsonify({"status": "processing_or_missing"}), 404
    resp = cached.copy()
    resp["from_cache"] = True
    return jsonify(resp)


@lyve_bp.app_errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return jsonify({"error": "File too large", "status": 413}), 413
