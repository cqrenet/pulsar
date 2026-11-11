"""Small helpers shared across routes, MCP tools and ingestion."""

import re
from datetime import UTC, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_z(dt: datetime) -> str:
    """UTC ISO-8601 with a Z suffix, the format stored events use."""
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def days_ago_iso(days: int) -> str:
    return iso_z(utc_now() - timedelta(days=days))


def contains(value) -> dict:
    """Mongo case-insensitive literal substring match. Escaping blocks regex injection and ReDoS."""
    return {"$regex": re.escape(str(value)), "$options": "i"}


def user_sub(user: dict | None, default: str = "anonymous") -> str:
    return (user or {}).get("sub") or default
