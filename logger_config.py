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

    # Console handler first, so the logger is always usable even if the file
    # handler cannot be created. On a hosted platform this is the stream the
    # operator actually reads.
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler, best effort.
    #
    # The log directory is gitignored, so it does not exist in a fresh
    # deployment, and some hosts mount the application read-only. Losing the
    # log file is a degradation; refusing to import is an outage — this used
    # to take the whole app down with a FileNotFoundError before a single line
    # of it ran.
    try:
        os.makedirs(Config.LOG_DIR, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(Config.LOG_DIR, "pdf_analyzer.log"),
            maxBytes=10485760,  # 10MB
            backupCount=5,
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.debug("File logging disabled (%s); logging to console only.", exc)

    return logger


# Create application logger
app_logger = setup_logger("pdf_analyzer")
