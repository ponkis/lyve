from flask import Flask
from flask_cors import CORS
import os
import threading
from werkzeug.middleware.proxy_fix import ProxyFix

from lyve.config import Config

__version__ = "1.0.0"

def create_app(config_class=Config):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(config_class)

    if app.config.get("TRUST_PROXY_HEADERS"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    cors_origins = [
        origin.strip()
        for origin in (app.config.get("CORS_ORIGINS") or "").split(",")
        if origin.strip()
    ]
    if cors_origins:
        CORS(app, origins=cors_origins)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        return response
    
    # Ensure directories exist
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["CACHE_FOLDER"], exist_ok=True)
    
    # Register blueprints
    from lyve.routes import lyve_bp
    app.register_blueprint(lyve_bp)
    
    # Initialize background threads
    from lyve.services.worker import cleanup_worker, queue_dispatcher
    
    try:
        cleaner = threading.Thread(target=cleanup_worker, daemon=True)
        cleaner.start()
        print("Started uploads cleaner thread.")
    except Exception as e:
        print(f"Failed to start cleanup thread: {e}")
        
    try:
        dispatcher = threading.Thread(target=queue_dispatcher, daemon=True)
        dispatcher.start()
        print("Started queue dispatcher thread.")
    except Exception as e:
        print(f"Failed to start queue dispatcher: {e}")
        
    return app
