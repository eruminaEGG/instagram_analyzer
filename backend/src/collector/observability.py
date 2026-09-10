"""Secret-safe structured standard logging."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_SENSITIVE_KEY = re.compile(r"(token|authorization|oauth|password|secret|code)$", re.I)
_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]*")
_API_ERROR_CODE = re.compile(r"[0-9]{1,10}")
_API_ERROR_TYPE = re.compile(r"[A-Za-z0-9_. -]{1,128}")
_FBTRACE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(access[_ -]?token|client[_ -]?secret|oauth[_ -]?token|authorization|password|secret)"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")


def safe_api_error_code(value: Any) -> str | None:
    """Return only the bounded numeric error identifier exposed by Graph API."""
    if isinstance(value, bool) or value is None:
        return None
    code = str(value)
    return code if _API_ERROR_CODE.fullmatch(code) else None


def safe_api_error_message(value: Any) -> str | None:
    """Bound and scrub Graph's human-readable message before structured logging."""
    if not isinstance(value, str):
        return None
    message = _URL_QUERY.sub(r"\1?[REDACTED]", value)
    message = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", message)
    message = _BEARER_VALUE.sub("Bearer [REDACTED]", message)
    return message[:500]


def safe_api_error_type(value: Any) -> str | None:
    return value if isinstance(value, str) and _API_ERROR_TYPE.fullmatch(value) else None


def safe_api_fbtrace_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _FBTRACE_ID.fullmatch(value) else None


def redact(value: Any, key: str | None = None) -> Any:
    """Remove credential-bearing fields, including nested URL query strings."""
    if key in {"api_error_code", "api_error_subcode"}:
        return safe_api_error_code(value)
    if key == "api_error_message":
        return safe_api_error_message(value)
    if key == "api_error_type":
        return safe_api_error_type(value)
    if key == "api_fbtrace_id":
        return safe_api_fbtrace_id(value)
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _URL_QUERY.sub(r"\1?[REDACTED]", value)
    return value


class JsonLogger:
    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("instagram_insights_collector")
        if isinstance(self.logger, logging.Logger):
            self.logger.setLevel(logging.INFO)

    def emit(self, event: str, **fields: Any) -> None:
        self.logger.info(json.dumps(redact({"event": event, **fields}), sort_keys=True, default=str))
