"""
Validators for file validation, API rate limiting, and timeout handling
"""
import os
from typing import Tuple, Optional
from datetime import datetime, timedelta
from config import Config
from logger_config import setup_logger

logger = setup_logger(__name__)


class FileValidator:
    """Validates uploaded PDF files"""

    @staticmethod
    def validate_file(file) -> Tuple[bool, str]:
        """
        Validate uploaded file
        
        Args:
            file: Streamlit UploadedFile object
        
        Returns:
            Tuple of (is_valid, message)
        """
        if not file:
            return False, "No file provided"

        # Check file extension
        file_name = file.name
        if not any(file_name.lower().endswith(ext) for ext in Config.ALLOWED_FILE_EXTENSIONS):
            return False, f"Invalid file type. Allowed types: {', '.join(Config.ALLOWED_FILE_EXTENSIONS)}"

        # Check file size
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)

        if file_size < Config.MIN_FILE_SIZE_BYTES:
            return False, f"File is too small. Minimum size: {Config.MIN_FILE_SIZE_BYTES} bytes"

        max_size_bytes = Config.MAX_FILE_SIZE_MB * 1024 * 1024
        if file_size > max_size_bytes:
            return False, f"File is too large. Maximum size: {Config.MAX_FILE_SIZE_MB} MB"

        logger.info(f"File validation passed: {file_name} ({file_size} bytes)")
        return True, "File validation passed"

    @staticmethod
    def get_file_size_mb(file) -> float:
        """Get file size in MB"""
        file.seek(0, os.SEEK_END)
        size_bytes = file.tell()
        file.seek(0)
        return size_bytes / (1024 * 1024)


class RateLimiter:
    """Manages API rate limiting"""

    def __init__(self, max_requests: int = Config.RATE_LIMIT_REQUESTS_PER_MINUTE):
        self.max_requests = max_requests
        self.request_times = []

    def is_allowed(self) -> bool:
        """Check if a request is allowed within rate limit"""
        now = datetime.now()
        cutoff_time = now - timedelta(minutes=1)

        # Remove old requests outside the 1-minute window
        self.request_times = [t for t in self.request_times if t > cutoff_time]

        if len(self.request_times) < self.max_requests:
            self.request_times.append(now)
            return True

        logger.warning(f"Rate limit exceeded: {self.max_requests} requests/minute")
        return False

    def get_retry_after(self) -> Optional[int]:
        """Get seconds until next request is allowed"""
        if not self.request_times:
            return None

        oldest_request = self.request_times[0]
        retry_time = oldest_request + timedelta(minutes=1)
        seconds_to_wait = (retry_time - datetime.now()).total_seconds()

        return max(0, int(seconds_to_wait)) if seconds_to_wait > 0 else None


class TimeoutManager:
    """Manages API call timeouts"""

    @staticmethod
    def get_timeout() -> float:
        """Get configured timeout in seconds"""
        return Config.API_TIMEOUT_SECONDS

    @staticmethod
    def get_timeout_message() -> str:
        """Get timeout warning message"""
        return f"Request timed out after {Config.API_TIMEOUT_SECONDS} seconds. Please try again."


# Global instances
file_validator = FileValidator()
rate_limiter = RateLimiter()
timeout_manager = TimeoutManager()
