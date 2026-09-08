import json
import unittest
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

from collector.errors import (
    AuthExpiredError,
    CollectionFailedError,
    MediaApiError,
    RateLimitedError,
)
from collector.instagram import InstagramClient
from collector.observability import JsonLogger, redact
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

        with self.assertRaises(RateLimitedError):
            InstagramClient("secret", opener=opener, sleep=lambda _: None).insights("m1", ["views"])

    def test_retry_exhaustion_is_rate_limited(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url, 429, "rate limited", {"Retry-After": "1"}, BytesIO()
            )

        with self.assertRaises(RateLimitedError):
            InstagramClient("secret", opener=opener, sleep=lambda _: None, max_retries=1).insights("m1", ["views"])

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
            failures={"m1": AuthExpiredError("access_token=super-secret")},
        )

        with self.assertRaises(CollectionFailedError):
            self.service(client, MemoryRepository(), JsonLogger(logger)).run(SLOT_EVENT)

        messages = " ".join(logger.messages)
        self.assertNotIn("super-secret", messages)
        self.assertIn("AUTH_EXPIRED", messages)
        self.assertEqual(redact({"access_token": "x", "url": "https://x/?token=y"}), {"access_token": "[REDACTED]", "url": "https://x/?[REDACTED]"})

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
