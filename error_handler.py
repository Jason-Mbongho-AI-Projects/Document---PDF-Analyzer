"""
Error handling and retry logic for API calls
"""
import time
from typing import Callable, Any, Optional, TypeVar
from functools import wraps
from config import Config
from logger_config import setup_logger

logger = setup_logger(__name__)

T = TypeVar("T")


class APIError(Exception):
    """Base exception for API errors"""
    pass


class RateLimitError(APIError):
    """Raised when API rate limit is exceeded"""
    pass


class TimeoutError(APIError):
    """Raised when API request times out"""
    pass


class RetryableError(APIError):
    """Raised for errors that should be retried"""
    pass


def retry_with_backoff(
    max_retries: int = Config.API_MAX_RETRIES,
    initial_delay: int = Config.API_RETRY_DELAY_SECONDS,
    backoff_factor: float = 2.0,
    exceptions: tuple = (RetryableError, RateLimitError),
) -> Callable:
    """
    Decorator for retrying failed function calls with exponential backoff
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        backoff_factor: Factor to multiply delay by on each retry
        exceptions: Tuple of exceptions to catch and retry on
    
    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            delay = initial_delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    logger.debug(f"Attempt {attempt + 1}/{max_retries + 1} for {func.__name__}")
                    return func(*args, **kwargs)

                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(f"Failed after {max_retries + 1} attempts: {str(e)}")
                        raise

                    logger.warning(
                        f"Attempt {attempt + 1} failed for {func.__name__}: {str(e)}. "
                        f"Retrying in {delay} seconds..."
                    )
                    time.sleep(delay)
                    delay *= backoff_factor

                except Exception as e:
                    logger.error(f"Non-retryable error in {func.__name__}: {str(e)}")
                    raise

            if last_exception:
                raise last_exception

        return wrapper

    return decorator


class ErrorHandler:
    """Handles and logs errors throughout the application"""

    @staticmethod
    def handle_api_error(error: Exception, context: str = "") -> str:
        """
        Handle API errors and return user-friendly message
        
        Args:
            error: The exception that occurred
            context: Context information about where the error occurred
        
        Returns:
            User-friendly error message
        """
        error_str = str(error)
        logger.error(f"API Error in {context}: {error_str}")

        if isinstance(error, RateLimitError):
            return "🚫 API rate limit exceeded. Please try again in a few moments."

        elif isinstance(error, TimeoutError):
            return "⏱️ Request timed out. The document may be too large. Try processing a smaller file."

        elif isinstance(error, APIError):
            return f"❌ API Error: {error_str}"

        else:
            return f"❌ Unexpected error: {error_str}"

    @staticmethod
    def handle_file_error(error: Exception, file_name: str = "") -> str:
        """
        Handle file processing errors
        
        Args:
            error: The exception that occurred
            file_name: Name of the file being processed
        
        Returns:
            User-friendly error message
        """
        error_str = str(error)
        logger.error(f"File Error for {file_name}: {error_str}")
        return f"❌ Error processing file '{file_name}': {error_str}"

    @staticmethod
    def handle_processing_error(error: Exception, context: str = "") -> str:
        """
        Handle processing errors
        
        Args:
            error: The exception that occurred
            context: Context information
        
        Returns:
            User-friendly error message
        """
        error_str = str(error)
        logger.error(f"Processing Error in {context}: {error_str}")
        return f"❌ Processing error: {error_str}"


# Global error handler instance
error_handler = ErrorHandler()
