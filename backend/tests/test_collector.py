import json
import logging
import unittest
from datetime import UTC, datetime
from io import BytesIO, StringIO
from pathlib import Path
from urllib.error import HTTPError

from collector.errors import (
    AuthExpiredError,
    CollectionFailedError,
    MediaApiError,
    RateLimitedError,
)
from collector.instagram import InstagramClient
from collector.observability import (
    JsonLogger,
    redact,
    safe_api_error_code,
    safe_api_error_message,
)
from collector.repository import DynamoRepository, observation_item
from collector.service import CollectorService
from collector.timebox import iso


SLOT_EVENT = {"scheduled_time": "2026-09-07T12:34:56Z"}
SLOT = "2026-09-07T12:00:00Z"


def reel(media_id: str, timestamp: str = "2026-09-01T12:00:00Z") -> dict:
    return {
        "id": media_id,
        "media_type": "VIDEO",
        "media_product_type": "REELS",
        "timestamp": timestamp,
    }


class FakeClient:
    def __init__(self, pages, answers=None, failures=None):
        self.pages = pages
        self.answers = answers or {}
        self.failures = failures or {}
        self.insight_calls = []

    def iter_media(self, account_id):
        for page in self.pages:
            yield from page["data"]

    def insights(self, media_id, requested_metrics):
        self.insight_calls.append(media_id)
        if media_id in self.failures:
            raise self.failures[media_id]
        return self.answers.get(media_id, ({"views": 10}, [], "trace-ignored"))


class MemoryRepository:
    def __init__(self):
        self.existing = set()
        self.items = []
        self.get_calls = []

    def observation_exists(self, media_id, slot):
        key = (media_id, iso(slot))
        self.get_calls.append(key)
        return key in self.existing

    def save_observation(self, item):
        key = (item["media_id"], item["slot_start"])
        if key in self.existing:
            return False
        self.existing.add(key)
        self.items.append(item)
        return True


class CapturingLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)


class FakeResponse(BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.headers = {}


class CapturingTable:
    def __init__(self, existing=False, conditional_failure=False):
        self.existing = existing
        self.conditional_failure = conditional_failure
        self.get_calls = []
        self.put_calls = []

    def get_item(self, **kwargs):
        self.get_calls.append(kwargs)
        return {"Item": {"PK": "existing"}} if self.existing else {}

    def put_item(self, **kwargs):
        self.put_calls.append(kwargs)
        if self.conditional_failure:
            error = type("ConditionalCheckFailedException", (Exception,), {})
            raise error("duplicate")


class CollectorTests(unittest.TestCase):
    def service(self, client, repository, logger=None):
        return CollectorService(
            client,
            repository,
            "ig-account",
            ["views", "reach"],
            "v26.0",
            logger=logger,
            clock=lambda: datetime(2026, 9, 7, 12, 1, tzinfo=UTC),
        )

    def test_real_client_follows_opaque_paging_next_to_terminal_page(self):
        media_pages = json.loads(
            (Path(__file__).parent / "fixtures" / "media_pages.json").read_text()
        )
        fixture = {
            "first": {"data": media_pages[0]["data"], "paging": {"next": "https://opaque.example/cursor?after=not-derived"}},
            "second": {"data": media_pages[1]["data"]},
        }
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            payload = fixture["first"] if len(calls) == 1 else fixture["second"]
            return FakeResponse(json.dumps(payload).encode())

        client = InstagramClient("super-secret", opener=opener, sleep=lambda _: None)
        media = list(client.iter_media("ig-account"))

        self.assertEqual([entry["id"] for entry in media], ["photo", "reel-1", "reel-2"])
        self.assertEqual(calls[1], fixture["first"]["paging"]["next"])
        self.assertNotIn("super-secret", " ".join(calls))

    def test_short_retry_after_then_success(self):
        attempts = []
        sleeps = []

        def opener(request, timeout):
            attempts.append(1)
            if len(attempts) == 1:
                raise HTTPError(
                    request.full_url,
                    429,
                    "rate limited",
                    {"Retry-After": "1"},
                    BytesIO(),
                )
            return FakeResponse(json.dumps({"data": [{"name": "views", "values": [{"value": 7}]}]}).encode())

        values, missing, _ = InstagramClient(
            "secret", opener=opener, sleep=sleeps.append, max_retries=1
        ).insights("m1", ["views"])
        self.assertEqual(values, {"views": 7})
        self.assertEqual(missing, [])
        self.assertEqual(sleeps, [1.0])

    def test_long_retry_after_is_rate_limited(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url, 429, "rate limited", {"Retry-After": "16"}, BytesIO()
            )

        with self.assertRaises(RateLimitedError) as caught:
            InstagramClient("secret", opener=opener, sleep=lambda _: None).insights("m1", ["views"])
        self.assertEqual(caught.exception.api_error_code, "429")

    def test_retry_exhaustion_is_rate_limited(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url, 429, "rate limited", {"Retry-After": "1"}, BytesIO()
            )

        with self.assertRaises(RateLimitedError):
            InstagramClient("secret", opener=opener, sleep=lambda _: None, max_retries=1).insights("m1", ["views"])

    def test_graph_error_metadata_is_preserved_without_response_body(self):
        payload = {
            "error": {
                "message": "Metric profile_visits is not supported for this media.",
                "type": "OAuthException",
                "code": 100,
                "error_subcode": 33,
                "fbtrace_id": "AbC_123-test",
                "access_token": "must-not-be-retained",
            }
        }

        def opener(request, timeout):
            raise HTTPError(
                request.full_url,
                400,
                "bad request",
                {"x-fb-trace-id": "header-trace"},
                BytesIO(json.dumps(payload).encode()),
            )

        with self.assertRaises(MediaApiError) as caught:
            InstagramClient("secret", opener=opener, sleep=lambda _: None).insights(
                "m1", ["views", "profile_visits"]
            )

        error = caught.exception
        self.assertEqual(error.api_error_code, "100")
        self.assertEqual(error.api_error_subcode, "33")
        self.assertEqual(error.api_error_type, "OAuthException")
        self.assertEqual(error.api_error_message, payload["error"]["message"])
        self.assertEqual(error.api_fbtrace_id, "AbC_123-test")
        self.assertFalse(hasattr(error, "access_token"))

    def test_exactly_30_days_old_media_is_excluded(self):
        client = FakeClient([{"data": [reel("boundary", "2026-08-08T12:00:00Z")]}])
        repository = MemoryRepository()

        result = self.service(client, repository).run(SLOT_EVENT)

        self.assertEqual(result["skipped_30_days"], 1)
        self.assertEqual(client.insight_calls, [])
        self.assertEqual(repository.items, [])

    def test_same_media_and_utc_slot_is_idempotent_before_insights(self):
        repository = MemoryRepository()
        first = FakeClient([{"data": [reel("m1")]}])
        self.service(first, repository).run(SLOT_EVENT)
        second = FakeClient([{"data": [reel("m1")]}])

        result = self.service(second, repository).run(SLOT_EVENT)

        self.assertEqual(len(repository.items), 1)
        self.assertEqual(second.insight_calls, [])
        self.assertEqual(result["already_saved"], 1)

    def test_missing_metric_creates_partial_item_without_zero_fill(self):
        client = FakeClient(
            [{"data": [reel("m1")]}],
            answers={"m1": ({"views": 10}, ["reach"], "trace")},
        )
        repository = MemoryRepository()

        result = self.service(client, repository).run(SLOT_EVENT)

        self.assertEqual(result["partial"], 1)
        item = repository.items[0]
        self.assertEqual(item["status"], "PARTIAL")
        self.assertEqual(item["missing_metrics"], ["reach"])
        self.assertNotIn("reach", item["metrics"])

    def test_media_api_failure_keeps_scanning_but_fails_lambda_without_error_item(self):
        client = FakeClient(
            [{"data": [reel("bad"), reel("good")]}],
            failures={"bad": MediaApiError("upstream detail")},
        )
        repository = MemoryRepository()

        with self.assertRaises(CollectionFailedError) as caught:
            self.service(client, repository).run(SLOT_EVENT)

        self.assertEqual(caught.exception.error_classes, {"MEDIA_API": 1})
        self.assertEqual([item["media_id"] for item in repository.items], ["good"])
        self.assertTrue(all("ERROR" not in item.values() for item in repository.items))

    def test_auth_and_rate_limit_failures_are_classified_after_partial_progress(self):
        client = FakeClient(
            [{"data": [reel("auth"), reel("rate"), reel("good")]}],
            failures={
                "auth": AuthExpiredError("token=not-for-log"),
                "rate": RateLimitedError("retry exhausted"),
            },
        )
        repository = MemoryRepository()

        with self.assertRaises(CollectionFailedError) as caught:
            self.service(client, repository).run(SLOT_EVENT)

        self.assertEqual(
            caught.exception.error_classes, {"AUTH_EXPIRED": 1, "RATE_LIMITED": 1}
        )
        self.assertEqual([item["media_id"] for item in repository.items], ["good"])

    def test_structured_logs_redact_secret_and_do_not_log_exception_message(self):
        logger = CapturingLogger()
        client = FakeClient(
            [{"data": [reel("m1")]}],
            failures={"m1": MediaApiError("access_token=super-secret", "190")},
        )

        with self.assertRaises(CollectionFailedError):
            self.service(client, MemoryRepository(), JsonLogger(logger)).run(SLOT_EVENT)

        messages = " ".join(logger.messages)
        self.assertNotIn("super-secret", messages)
        failed_media = next(
            json.loads(message)
            for message in logger.messages
            if json.loads(message)["event"] == "media_collection_failed"
        )
        failed_collection = next(
            json.loads(message)
            for message in logger.messages
            if json.loads(message)["event"] == "collection_failed"
        )
        self.assertEqual(
            failed_media,
            {
                "api_error_code": "190",
                "error_class": "MEDIA_API",
                "event": "media_collection_failed",
                "media_id": "m1",
                "slot_start": SLOT,
            },
        )
        self.assertEqual(failed_collection["error_classes"], {"MEDIA_API": 1})
        self.assertEqual(failed_collection["failed"], 1)
        self.assertEqual(redact({"access_token": "x", "url": "https://x/?token=y"}), {"access_token": "[REDACTED]", "url": "https://x/?[REDACTED]"})

    def test_structured_logs_include_scrubbed_graph_error_metadata(self):
        logger = CapturingLogger()
        client = FakeClient(
            [{"data": [reel("m1")]}],
            failures={
                "m1": MediaApiError(
                    "generic internal message",
                    "100",
                    api_error_message=(
                        "Unsupported request; access_token=super-secret "
                        "https://graph.instagram.com/x?access_token=also-secret"
                    ),
                    api_error_type="OAuthException",
                    api_error_subcode="33",
                    api_fbtrace_id="AbC_123-test",
                )
            },
        )

        with self.assertRaises(CollectionFailedError):
            self.service(client, MemoryRepository(), JsonLogger(logger)).run(SLOT_EVENT)

        failed_media = next(
            json.loads(message)
            for message in logger.messages
            if json.loads(message)["event"] == "media_collection_failed"
        )
        self.assertEqual(failed_media["api_error_code"], "100")
        self.assertEqual(failed_media["api_error_subcode"], "33")
        self.assertEqual(failed_media["api_error_type"], "OAuthException")
        self.assertEqual(failed_media["api_fbtrace_id"], "AbC_123-test")
        self.assertIn("access_token=[REDACTED]", failed_media["api_error_message"])
        self.assertNotIn("super-secret", json.dumps(failed_media))
        self.assertNotIn("also-secret", json.dumps(failed_media))

    def test_default_json_logger_enables_info_and_writes_json(self):
        logger = logging.getLogger("instagram_insights_collector")
        previous_level = logger.level
        previous_handlers = logger.handlers[:]
        previous_propagate = logger.propagate
        stream = StringIO()
        try:
            logger.handlers = [logging.StreamHandler(stream)]
            logger.propagate = False
            logger.setLevel(logging.WARNING)

            JsonLogger().emit("collection_started", slot_start=SLOT)

            self.assertEqual(logger.getEffectiveLevel(), logging.INFO)
            self.assertEqual(
                json.loads(stream.getvalue()),
                {"event": "collection_started", "slot_start": SLOT},
            )
        finally:
            logger.setLevel(previous_level)
            logger.handlers = previous_handlers
            logger.propagate = previous_propagate

    def test_api_error_code_accepts_only_bounded_numeric_identifier(self):
        self.assertEqual(safe_api_error_code(190), "190")
        self.assertEqual(redact({"api_error_code": "429"}), {"api_error_code": "429"})
        self.assertIsNone(safe_api_error_code("190 token=super-secret"))
        self.assertEqual(
            redact({"api_error_code": "190 token=super-secret"}),
            {"api_error_code": None},
        )
        self.assertEqual(
            safe_api_error_message("Bearer abc access_token=xyz"),
            "Bearer [REDACTED] access_token=[REDACTED]",
        )

    def test_api_error_message_redacts_common_credential_assignments(self):
        dummy_secret = "dummy-sensitive-value"

        for message in (
            f"Authorization: Bearer {dummy_secret}",
            f"client_secret={dummy_secret}",
            f"oauth_token={dummy_secret}",
        ):
            with self.subTest(message=message.split("=", 1)[0]):
                sanitized = safe_api_error_message(message)
                self.assertNotIn(dummy_secret, sanitized)
                self.assertIn("[REDACTED]", sanitized)

    def test_dynamo_mapper_uses_only_observation_key_and_conditional_put(self):
        table = CapturingTable()
        repository = DynamoRepository(table)
        item = observation_item(
            reel("m1"),
            "ig-account",
            datetime(2026, 9, 7, 12, tzinfo=UTC),
            datetime(2026, 9, 7, 12, 1, tzinfo=UTC),
            "v26.0",
            {"views": 10},
            [],
        )

        self.assertFalse(repository.observation_exists("m1", datetime(2026, 9, 7, 12, tzinfo=UTC)))
        self.assertTrue(repository.save_observation(item))

        self.assertEqual(table.get_calls[0], {"Key": {"PK": "MEDIA#m1", "SK": "OBS#" + SLOT}, "ConsistentRead": True})
        self.assertEqual(table.put_calls[0]["ConditionExpression"], "attribute_not_exists(PK) AND attribute_not_exists(SK)")
        self.assertEqual(set(item), {"PK", "SK", "instagram_account_id", "media_id", "media_type", "media_product_type", "published_at", "slot_start", "observed_at", "elapsed_seconds", "metrics", "missing_metrics", "api_version", "status"})

    def test_conditional_put_collision_is_idempotent(self):
        table = CapturingTable(conditional_failure=True)
        repository = DynamoRepository(table)
        item = {"PK": "MEDIA#m1", "SK": "OBS#" + SLOT}

        self.assertFalse(repository.save_observation(item))


if __name__ == "__main__":
    unittest.main()
