"""
Configuration management for PDF Analyzer
"""
import os
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Central configuration for the PDF Analyzer application"""

    # API Configuration
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

    # Processing Configuration
    MAX_TOKENS_PER_CHUNK = 8000
    MIN_TOKENS_PER_CHUNK = 2000
    DEFAULT_SUMMARY_TYPE = "detailed"
    SUPPORTED_SUMMARY_TYPES = ["brief", "detailed", "bullet_points", "executive"]

    # File Validation
    MAX_FILE_SIZE_MB = 50
    ALLOWED_FILE_EXTENSIONS = [".pdf"]
    MIN_FILE_SIZE_BYTES = 1024  # 1KB minimum

    # API Limits
    API_TIMEOUT_SECONDS = 60
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 2
    RATE_LIMIT_REQUESTS_PER_MINUTE = 60

    # Caching
    ENABLE_CACHING = True
    CACHE_EXPIRATION_HOURS = 24
    CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")

    # Session Management
    ENABLE_SESSION_HISTORY = True
    SESSION_HISTORY_DIR = os.path.join(os.path.dirname(__file__), ".session_history")
    MAX_SESSION_HISTORY_ITEMS = 50

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
    LOG_FILE_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # UI Configuration
    ENABLE_DARK_MODE = True
    THEME_PRIMARY_COLOR = "#0078D4"
    ENABLE_ADVANCED_FEATURES = True

    # Processing
    ENABLE_KEYWORD_EXTRACTION = True
    ENABLE_SENTIMENT_ANALYSIS = True
    ENABLE_READABILITY_SCORE = True
    ENABLE_TABLE_OF_CONTENTS = True

    # Batch Processing
    ENABLE_BATCH_PROCESSING = True
    MAX_BATCH_SIZE = 10

    # Performance
    ENABLE_PARALLEL_PROCESSING = True
    NUM_WORKER_THREADS = 4

    @classmethod
    def to_dict(cls) -> Dict[str, Any]:
        """Convert configuration to dictionary"""
        return {
            key: getattr(cls, key)
            for key in dir(cls)
            if not key.startswith("_") and key.isupper()
        }

    @classmethod
    def validate(cls) -> bool:
        """Validate critical configuration"""
        if not cls.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY not set in environment")
        
        os.makedirs(cls.CACHE_DIR, exist_ok=True)
        os.makedirs(cls.SESSION_HISTORY_DIR, exist_ok=True)
        os.makedirs(cls.LOG_DIR, exist_ok=True)
        
        return True


# Validate on import
try:
    Config.validate()
except ValueError as e:
    import warnings
    warnings.warn(f"Configuration warning: {str(e)}")
