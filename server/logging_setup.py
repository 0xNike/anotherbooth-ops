from __future__ import annotations

import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "service": "anotherbooth-server",
            "message": record.getMessage(),
        }
        for key in (
            "session_id",
            "room_id",
            "shot_id",
            "event",
            "result",
            "latency_ms",
            "error_code",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str)


def configure_logging(log_dir: Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if root.handlers:
        return

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(JsonFormatter())
    root.addHandler(stream_handler)

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=log_dir / "anotherbooth.log",
            when="midnight",
            backupCount=14,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)
