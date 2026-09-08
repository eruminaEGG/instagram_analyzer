"""Single-Lambda collection orchestration for observation items only."""

from datetime import UTC, datetime
from typing import Any, Callable

from .errors import CollectionFailedError, CollectorError
from .observability import JsonLogger
from .repository import Repository, is_reel, observation_item
from .timebox import is_collectable, iso, parse_utc, slot_start


class CollectorService:
    def __init__(
        self,
        client: Any,
        repository: Repository,
        account_id: str,
        insight_metrics: list[str],
        api_version: str,
        logger: JsonLogger | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.repository = repository
        self.account_id = account_id
        self.insight_metrics = insight_metrics
        self.api_version = api_version
        self.logger = logger or JsonLogger()
        self.clock = clock or (lambda: datetime.now(UTC))

    def run(self, event: dict[str, Any]) -> dict[str, int | str]:
        scheduled_time = event.get("scheduled_time")
        if not isinstance(scheduled_time, str):
            raise ValueError("scheduled_time is required")
        slot = slot_start(scheduled_time)
        counts: dict[str, int] = {
            "media_seen": 0,
            "eligible": 0,
            "saved": 0,
            "partial": 0,
            "already_saved": 0,
            "skipped_30_days": 0,
            "failed": 0,
        }
        error_classes: dict[str, int] = {}
        self.logger.emit("collection_started", slot_start=iso(slot))

        for media in self.client.iter_media(self.account_id):
            counts["media_seen"] += 1
            if not is_reel(media):
                continue
            if not is_collectable(parse_utc(media["timestamp"]), slot):
                counts["skipped_30_days"] += 1
                continue
            if self.repository.observation_exists(media["id"], slot):
                counts["already_saved"] += 1
                continue

            counts["eligible"] += 1
            try:
                values, missing_metrics, _ = self.client.insights(
                    media["id"], self.insight_metrics
                )
                item = observation_item(
                    media=media,
                    account_id=self.account_id,
                    slot=slot,
                    observed_at=self.clock(),
                    api_version=self.api_version,
                    metrics=values,
                    missing_metrics=missing_metrics,
                )
                if self.repository.save_observation(item):
                    counts["saved"] += 1
                    if missing_metrics:
                        counts["partial"] += 1
                else:
                    # A concurrent invocation inserted the exact same observation.
                    counts["already_saved"] += 1
            except CollectorError as exc:
                counts["failed"] += 1
                error_classes[exc.error_class] = error_classes.get(exc.error_class, 0) + 1
                self.logger.emit(
                    "media_collection_failed",
                    slot_start=iso(slot),
                    media_id=media["id"],
                    error_class=exc.error_class,
                )

        summary: dict[str, int | str] = {"slot_start": iso(slot), **counts}
        if error_classes:
            self.logger.emit("collection_failed", **summary, error_classes=error_classes)
            raise CollectionFailedError(error_classes)
        self.logger.emit("collection_finished", **summary)
        return summary
