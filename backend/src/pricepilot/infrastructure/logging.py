"""Structured, redacting logging.

Logs never contain request/response bodies, query strings, document content or secrets.
Registered secrets (e.g. the API token) are scrubbed from every record as defence in depth.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_SECRETS: set[str] = set()
_REDACTED = "[REDACTED]"
_SAFE_EXTRA_KEYS = ("method", "path", "status", "duration_ms", "error_code", "event", "port")


def register_secret(value: str) -> None:
    if value:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for secret in _SECRETS:
        text = text.replace(secret, _REDACTED)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        correlation_id = getattr(record, "correlation_id", None) or correlation_id_var.get()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        for key in _SAFE_EXTRA_KEYS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info and record.exc_info[0] is not None:
            # Exception type only: tracebacks may embed values or local file paths.
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str, log_dir: Path | None) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    formatter = JsonFormatter()
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(formatter)
    root.addHandler(stderr)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "api.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    root.setLevel(level.upper())
    # Uvicorn's own access log includes query strings; we emit our own access record instead.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("alembic").setLevel(logging.WARNING)
