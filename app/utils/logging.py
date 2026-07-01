"""
Logging configuration for the Screensaver application.

Provides structured logging with configurable log levels via environment variables.
"""

import logging
import sys
import os
from collections import deque
from typing import Any, Deque, Dict, List, Optional


# In-memory ring buffer of recent log records, surfaced by the admin Logs panel
# (GET /api/system/logs). Bounded so a long-running process can't grow it; no
# disk writes. Single-process uvicorn ⇒ this is the complete recent history.
_LOG_BUFFER_MAX = int(os.environ.get("LOG_BUFFER_MAX", "2000"))
_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


class RingBufferLogHandler(logging.Handler):
    """Keep the last N log records in memory as structured dicts."""

    def __init__(self, capacity: int = _LOG_BUFFER_MAX):
        super().__init__()
        self.records: Deque[Dict[str, Any]] = deque(maxlen=capacity)
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._seq += 1
            self.records.append({
                "seq": self._seq,
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record) if self.formatter else record.getMessage(),
            })
        except Exception:  # never let logging raise
            pass

    def query(
        self,
        level: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Filtered tail, oldest-first. `level` is a minimum level."""
        min_lvl = _LEVEL_ORDER.get((level or "").upper(), 0)
        needle = (q or "").lower()
        out = [
            r for r in self.records
            if _LEVEL_ORDER.get(r["level"], 0) >= min_lvl
            and (not needle or needle in r["message"].lower() or needle in r["logger"].lower())
        ]
        if limit and len(out) > limit:
            out = out[-limit:]
        return out


# Module-level singleton so routes can read the buffer without app state.
log_buffer = RingBufferLogHandler()


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

    # The ring buffer stores just the message (client renders ts/level/logger).
    log_buffer.setFormatter(logging.Formatter('%(message)s'))
    log_buffer.setLevel(logging.DEBUG)

    # Configure root logger (force=True ensures config applies even if called multiple times)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout), log_buffer],
        force=True
    )

    # Get logger for this application
    logger = logging.getLogger(name)

    return logger


# Create module-level logger instance
logger = setup_logging()
