"""Instagram Login Graph API client with cursor-complete media discovery."""

from __future__ import annotations

import json
import random
import time
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .errors import AuthExpiredError, MediaApiError, RateLimitedError, TransientError


class InstagramClient:
    def __init__(self, token: str, api_version: str = "v26.0", host: str = "https://graph.instagram.com", timeout_seconds: int = 10, opener: Callable[..., Any] = urlopen, sleep: Callable[[float], None] = time.sleep, max_retries: int = 3):
        self._token, self._api_version, self._host = token, api_version, host.rstrip("/")
        self._timeout, self._opener, self._sleep = timeout_seconds, opener, sleep
        self._max_retries = max_retries

    def iter_media(self, account_id: str) -> Iterator[dict[str, Any]]:
        path = f"/{self._api_version}/{account_id}/media"
        params: dict[str, str] | None = {"fields": "id,media_type,media_product_type,timestamp,permalink", "limit": "100"}
        while path:
            body, headers = self._request(path, params)
            for media in body.get("data", []):
                if isinstance(media, dict):
                    yield media
            next_url = body.get("paging", {}).get("next")
            # Follow the opaque API cursor, never infer ordering or manufacture a cursor.
            if next_url:
                path, params = next_url, None
            else:
                path = ""

    def insights(self, media_id: str, metrics: list[str]) -> tuple[dict[str, float], list[str], str | None]:
        body, headers = self._request(f"/{self._api_version}/{media_id}/insights", {"metric": ",".join(metrics)})
        values: dict[str, float] = {}
        returned = body.get("data", [])
        for entry in returned:
            name = entry.get("name")
            data = entry.get("values", [])
            if name in metrics and data and isinstance(data[0].get("value"), (int, float)):
                values[name] = data[0]["value"]
        missing = [metric for metric in metrics if metric not in values]
        return values, missing, headers.get("x-fb-trace-id")

    def _request(self, path_or_url: str, params: dict[str, str] | None) -> tuple[dict[str, Any], dict[str, str]]:
        url = path_or_url if path_or_url.startswith("http") else self._host + path_or_url
        if params is not None:
            url += ("&" if "?" in url else "?") + urlencode(params)
        # Token is an Authorization header; it cannot be copied into a query-string log.
        request = Request(url, headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"})
        for attempt in range(self._max_retries + 1):
            try:
                with self._opener(request, timeout=self._timeout) as response:
                    return json.loads(response.read().decode("utf-8")), {k.lower(): v for k, v in response.headers.items()}
            except HTTPError as exc:
                payload = self._error_payload(exc)
                error_fields = self._error_fields(payload, exc)
                # Meta commonly signals an expired/invalid token as HTTP 401 or error code 190.
                if exc.code == 401 or str(payload.get("code")) == "190":
                    raise AuthExpiredError(
                        "Instagram authentication failed",
                        **error_fields,
                    ) from exc
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after is not None else None
                    except ValueError:
                        delay = None
                    if delay is not None and 0 < delay <= 15 and attempt < self._max_retries:
                        self._sleep(delay)
                        continue
                    raise RateLimitedError(
                        "Instagram API rate limited",
                        **error_fields,
                    ) from exc
                if 500 <= exc.code < 600 and attempt < self._max_retries:
                    self._sleep((2**attempt) + random.random())
                    continue
                raise MediaApiError(
                    "Instagram API rejected media request",
                    **error_fields,
                ) from exc
            except (URLError, TimeoutError) as exc:
                if attempt < self._max_retries:
                    self._sleep((2**attempt) + random.random())
                    continue
                raise TransientError("Instagram API transport failure") from exc
        raise AssertionError("unreachable")

    @staticmethod
    def _error_payload(exc: HTTPError) -> dict[str, Any]:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            return body.get("error", {}) if isinstance(body, dict) else {}
        except Exception:
            return {}
        finally:
            exc.close()

    @staticmethod
    def _error_fields(payload: dict[str, Any], exc: HTTPError) -> dict[str, str | None]:
        """Extract only documented Graph error metadata; never retain the body or token."""
        headers = exc.headers or {}
        return {
            "api_error_code": str(payload.get("code", exc.code)),
            "api_error_message": payload.get("message") if isinstance(payload.get("message"), str) else None,
            "api_error_type": payload.get("type") if isinstance(payload.get("type"), str) else None,
            "api_error_subcode": str(payload["error_subcode"]) if payload.get("error_subcode") is not None else None,
            "api_fbtrace_id": (
                payload.get("fbtrace_id")
                if isinstance(payload.get("fbtrace_id"), str)
                else headers.get("x-fb-trace-id") or headers.get("X-FB-Trace-ID")
            ),
        }
