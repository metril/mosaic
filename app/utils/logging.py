"""
Logging configuration for the Screensaver application.

Provides structured logging with configurable log levels via environment variables.
"""

import logging
import sys
import os
from typing import Optional


def setup_logging(name: str = 'screensaver') -> logging.Logger:
    """
    Configure and return the application logger.

    Reads LOG_LEVEL from environment (default: INFO).
    Outputs to stdout with timestamp, name, level, and message.

    Args:
        name: Logger name (default: 'screensaver')

    Returns:
        Configured Logger instance
    """
    log_level_str = os.environ.get('LOG_LEVEL', 'INFO').upper()

    # Map string to logging level, default to INFO if invalid
    log_level = getattr(logging, log_level_str, logging.INFO)

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    # Get logger for this application
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    return logger


# Create module-level logger instance
logger = setup_logging()
