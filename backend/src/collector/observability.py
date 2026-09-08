"""Secret-safe structured standard logging."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_SENSITIVE_KEY = re.compile(r"(token|authorization|oauth|password|secret|code)$", re.I)
_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]*")


def redact(value: Any, key: str | None = None) -> Any:
    """Remove credential-bearing fields, including nested URL query strings."""
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

    def emit(self, event: str, **fields: Any) -> None:
        self.logger.info(json.dumps(redact({"event": event, **fields}), sort_keys=True, default=str))
