import os

class Config:
    # Directories
    UPLOAD_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))
    CACHE_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cache"))
    
    # File Constraints
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB
    ALLOWED_EXTENSIONS = {"mp3", "wav", "flac", "ogg", "m4a"}
    
    # Time-To-Live Settings
    UPLOAD_TTL_SECONDS = 5 * 60  # 5 minutes
    CACHE_TTL_DAYS = 7
    
    # Model Configuration
    WHISPER_MODEL_SIZE = "base"
    WHISPER_DEVICE = "cpu"
    WHISPER_COMPUTE_TYPE = "int8"
