"""
Logging configuration for PDF Analyzer
"""
import logging
import logging.handlers
import os
from config import Config


def setup_logger(name: str) -> logging.Logger:
    """
    Set up a logger with file and console handlers
    
    Args:
        name: Logger name (usually __name__)
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(Config.LOG_LEVEL)

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    # Create formatters
    formatter = logging.Formatter(Config.LOG_FILE_FORMAT)

    # File handler
    log_file = os.path.join(Config.LOG_DIR, "pdf_analyzer.log")
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10485760,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


# Create application logger
app_logger = setup_logger("pdf_analyzer")
