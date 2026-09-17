"""In-memory ring buffer so the web GUI can show recent log lines."""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Any

LOG_FORMAT = "[%(levelname)s] %(name)s: %(message)s"


class RingBufferHandler(logging.Handler):
    """Keeps the last N log records in memory."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._records: deque[dict[str, Any]] = deque(maxlen=max(10, capacity))
        self._counter = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message = f"{message}\n{self.format(record)}"
        except Exception:  # pragma: no cover - never let logging break the app
            message = "<unformattable log record>"
        with self._lock:
            self._counter += 1
            self._records.append(
                {
                    "id": self._counter,
                    "ts": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": message,
                }
            )

    def entries(self, since: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            items = [item for item in self._records if item["id"] > since]
        return items[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def resize(self, capacity: int) -> None:
        with self._lock:
            self._records = deque(self._records, maxlen=max(10, capacity))


_buffer: RingBufferHandler | None = None


def setup_logging(level: str = "INFO", capacity: int = 500) -> RingBufferHandler:
    """Configure root logging with a console handler plus the ring buffer."""
    global _buffer
    numeric = getattr(logging, str(level).upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric)

    if _buffer is None:
        _buffer = RingBufferHandler(capacity)
        _buffer.setFormatter(logging.Formatter(LOG_FORMAT))
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(console)
        root.addHandler(_buffer)
    else:
        _buffer.resize(capacity)

    # Third party libraries are chatty at DEBUG level.
    logging.getLogger("pymodbus").setLevel(max(numeric, logging.INFO))
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return _buffer


def get_buffer() -> RingBufferHandler | None:
    return _buffer


def set_level(level: str) -> None:
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.getLogger().setLevel(numeric)
    logging.getLogger("pymodbus").setLevel(max(numeric, logging.INFO))
