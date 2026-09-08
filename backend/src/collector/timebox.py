"""UTC slot and 30-day collection eligibility rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slot_start(scheduled_time: str) -> datetime:
    value = parse_utc(scheduled_time)
    return value.replace(minute=0, second=0, microsecond=0)


def is_collectable(published_at: datetime, slot: datetime) -> bool:
    return slot < published_at + timedelta(days=30)
