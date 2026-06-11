import os


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    # Directories
    UPLOAD_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))
    CACHE_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cache"))
    
    # File Constraints
    MAX_CONTENT_LENGTH = _env_int("LYVE_MAX_UPLOAD_BYTES", 50 * 1024 * 1024)
    MAX_FORM_MEMORY_SIZE = _env_int("LYVE_MAX_FORM_MEMORY_BYTES", 1024 * 1024)
    MAX_AUDIO_DURATION_SECONDS = _env_int("LYVE_MAX_AUDIO_DURATION_SECONDS", 4 * 60 + 30)
    ALLOWED_AUDIO_EXTENSIONS = {"mp3", "wav", "flac", "ogg", "m4a"}
    ALLOWED_EXTENSIONS = ALLOWED_AUDIO_EXTENSIONS
    ALLOWED_AUDIO_MIME_TYPES = {
        "audio/aac",
        "audio/flac",
        "audio/m4a",
        "audio/mp4",
        "audio/mpeg",
        "audio/mp3",
        "audio/ogg",
        "audio/wav",
        "audio/wave",
        "audio/x-flac",
        "audio/x-m4a",
        "audio/x-mpeg",
        "audio/x-wav",
        "application/ogg",
        "video/mp4",
    }
    GENERIC_UPLOAD_MIME_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
    
    # Time-To-Live Settings
    UPLOAD_TTL_SECONDS = 5 * 60  # 5 minutes
    CACHE_TTL_DAYS = 7

    # Public site metadata
    PUBLIC_BASE_URL = os.environ.get("LYVE_PUBLIC_BASE_URL", "").rstrip("/")
    SITE_NAME = "Lyve"
    SITE_TITLE = "Lyve"
    SITE_DESCRIPTION = (
        "Upload a song and turn it into a real-time lyric visualizer with synced lyrics, "
        "BPM detection, and downloadable .ponk sessions."
    )
    SOCIAL_IMAGE = "lyve-social-card.png"

    # Leave empty for same-origin only. Set comma-separated origins if a separate frontend needs API access.
    CORS_ORIGINS = os.environ.get("LYVE_CORS_ORIGINS", "")
    TRUST_PROXY_HEADERS = os.environ.get("LYVE_TRUST_PROXY_HEADERS", "false").lower() == "true"
    DEBUG_UPLOAD_LOGS = os.environ.get("LYVE_DEBUG_UPLOAD_LOGS", "false").lower() == "true"
    
    # Model Configuration
    WHISPER_MODEL_SIZE = "base"
    WHISPER_DEVICE = "cpu"
    WHISPER_COMPUTE_TYPE = "int8"
