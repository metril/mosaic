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
    log_level = getattr(logging, log_level_str, None)
    if log_level is None:
        # Invalid log level - warn and use INFO
        log_level = logging.INFO
        print(f"Warning: Invalid LOG_LEVEL '{log_level_str}', using INFO", file=sys.stderr)

    # Configure root logger (force=True ensures config applies even if called multiple times)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True
    )

    # Get logger for this application
    logger = logging.getLogger(name)

    return logger


# Create module-level logger instance
logger = setup_logging()
