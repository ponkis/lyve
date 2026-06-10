from flask import Flask
from flask_cors import CORS
import os
import threading

from lyve.config import Config

__version__ = "1.0.0"

def create_app(config_class=Config):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(config_class)
    
    CORS(app)
    
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
