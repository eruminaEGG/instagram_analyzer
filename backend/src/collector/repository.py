"""DynamoDB mappings for observations, post indexes, and future final analyses."""

from datetime import datetime, timedelta
from typing import Any, Protocol

from .timebox import iso, parse_utc


class Repository(Protocol):
    """Storage operations required by the single collector Lambda."""

    def observation_exists(self, media_id: str, slot: datetime) -> bool: ...

    def save_observation(self, item: dict[str, Any]) -> bool: ...

    def upsert_post_index(self, item: dict[str, Any]) -> bool: ...

    def save_final_analysis(self, item: dict[str, Any]) -> bool: ...


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


def post_index_item(media: dict[str, Any], account_id: str) -> dict[str, Any]:
    """Map a Reel into the account-scoped post list without an observation slot."""
    published_at = iso(parse_utc(media["timestamp"]))
    analysis_due_at = iso(parse_utc(media["timestamp"]) + timedelta(days=30))
    item: dict[str, Any] = {
        "PK": f"ACCOUNT#{account_id}",
        "SK": f"MEDIA#{published_at}#{media['id']}",
        "media_id": media["id"],
        "instagram_account_id": account_id,
        "published_at": published_at,
        "permalink": media.get("permalink"),
        "media_type": media["media_type"],
        "media_product_type": media["media_product_type"],
        "analysis_due_at": analysis_due_at,
        "analysis_status": "pending",
    }
    # Caption is API-optional and must never block index or insight collection.
    if isinstance(media.get("caption"), str):
        item["caption"] = media["caption"]
    return item


def final_analysis_item(
    media_id: str,
    version: str,
    generated_at: datetime,
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """Build the future AI-owned final-analysis record; this collector never calls AI."""
    if not version:
        raise ValueError("analysis version is required")
    return {
        "PK": f"MEDIA#{media_id}",
        "SK": f"ANALYSIS#FINAL#{version}",
        "media_id": media_id,
        "analysis_type": "FINAL",
        "analysis_version": version,
        "generated_at": iso(generated_at),
        "analysis": analysis,
    }


class DynamoRepository:
    """DynamoDB implementation with immutable observations and isolated indexes."""

    def __init__(self, table: Any):
        self.table = table

    def observation_exists(self, media_id: str, slot: datetime) -> bool:
        result = self.table.get_item(
            Key={"PK": f"MEDIA#{media_id}", "SK": f"OBS#{iso(slot)}"},
            ConsistentRead=True,
        )
        return "Item" in result

    def save_observation(self, item: dict[str, Any]) -> bool:
        return self._conditional_put(item)

    def save_final_analysis(self, item: dict[str, Any]) -> bool:
        """Reserve a final-analysis version without ever replacing an observation."""
        return self._conditional_put(item)

    def _conditional_put(self, item: dict[str, Any]) -> bool:
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

    def upsert_post_index(self, item: dict[str, Any]) -> bool:
        """Create or refresh a list item using the existing GetItem/PutItem IAM.

        A refresh preserves the status read from DynamoDB and conditionally writes
        against it, so a collector retry cannot reset a concurrently completed
        final-analysis status.  A conditional collision is harmless: another
        invocation has already supplied the same index key.
        """
        key = {"PK": item["PK"], "SK": item["SK"]}
        existing = self.table.get_item(Key=key, ConsistentRead=True).get("Item")
        if not existing:
            return self._conditional_put(item)

        refreshed = {**existing, **item}
        refreshed["analysis_status"] = existing.get("analysis_status", "pending")
        try:
            self.table.put_item(
                Item=refreshed,
                ConditionExpression=(
                    "attribute_exists(PK) AND attribute_exists(SK) "
                    "AND #analysis_status = :expected_status"
                ),
                ExpressionAttributeNames={"#analysis_status": "analysis_status"},
                ExpressionAttributeValues={":expected_status": refreshed["analysis_status"]},
            )
            return True
        except Exception as exc:
            if "ConditionalCheckFailed" in type(exc).__name__:
                return False
            raise
