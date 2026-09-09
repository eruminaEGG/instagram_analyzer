"""Stable error classifications used by storage, logs, and metrics."""

from __future__ import annotations


class CollectorError(Exception):
    error_class = "CODE_CONFIG"
    systemic = True

    def __init__(self, message: str, api_error_code: str | None = None):
        super().__init__(message)
        self.api_error_code = api_error_code


class TransientError(CollectorError):
    error_class = "TRANSIENT"


class RateLimitedError(CollectorError):
    error_class = "RATE_LIMITED"


class AuthExpiredError(CollectorError):
    error_class = "AUTH_EXPIRED"


class MediaApiError(CollectorError):
    """A permanent error isolated to one media item."""

    error_class = "MEDIA_API"
    systemic = False

class CollectionFailedError(CollectorError):
    """Raised after the complete media scan when one or more media failed."""

    error_class = "COLLECTION_FAILED"
    systemic = True

    def __init__(self, error_classes: dict[str, int]):
        super().__init__("one or more media could not be collected")
        self.error_classes = error_classes
