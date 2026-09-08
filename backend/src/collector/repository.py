"""DynamoDB observation-only mapper and idempotency boundary."""

from datetime import datetime
from typing import Any, Protocol

from .timebox import iso


class Repository(Protocol):
    """Storage operations required by the single collector Lambda."""

    def observation_exists(self, media_id: str, slot: datetime) -> bool: ...

    def save_observation(self, item: dict[str, Any]) -> bool: ...


def is_reel(media: dict[str, Any]) -> bool:
    return (
        media.get("media_type") == "VIDEO"
        and media.get("media_product_type") == "REELS"
    )


def observation_item(
    media: dict[str, Any],
    account_id: str,
    slot: datetime,
    observed_at: datetime,
    api_version: str,
    metrics: dict[str, int | float],
    missing_metrics: list[str],
) -> dict[str, Any]:
    """Map one successfully fetched insight response to the approved item schema."""
    published_at = iso(datetime.fromisoformat(media["timestamp"].replace("Z", "+00:00")))
    elapsed_seconds = int(
        (slot - datetime.fromisoformat(published_at.replace("Z", "+00:00"))).total_seconds()
    )
    return {
        "PK": f"MEDIA#{media['id']}",
        "SK": f"OBS#{iso(slot)}",
        "instagram_account_id": account_id,
        "media_id": media["id"],
        "media_type": media["media_type"],
        "media_product_type": media["media_product_type"],
        "published_at": published_at,
        "slot_start": iso(slot),
        "observed_at": iso(observed_at),
        "elapsed_seconds": elapsed_seconds,
        "metrics": metrics,
        "missing_metrics": missing_metrics,
        "api_version": api_version,
        "status": "PARTIAL" if missing_metrics else "SUCCESS",
    }


class DynamoRepository:
    """DynamoDB implementation using only observation GetItem and PutItem."""

    def __init__(self, table: Any):
        self.table = table

    def observation_exists(self, media_id: str, slot: datetime) -> bool:
        result = self.table.get_item(
            Key={"PK": f"MEDIA#{media_id}", "SK": f"OBS#{iso(slot)}"},
            ConsistentRead=True,
        )
        return "Item" in result

    def save_observation(self, item: dict[str, Any]) -> bool:
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
            return True
        except Exception as exc:  # boto3 provides this generated exception at runtime.
            if "ConditionalCheckFailed" in type(exc).__name__:
                return False
            raise
