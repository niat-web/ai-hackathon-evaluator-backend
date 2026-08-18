"""
India Standard Time (IST, Asia/Kolkata, UTC+05:30) helpers.

All application timestamps are written and returned in IST so the India-facing
product shows a consistent clock everywhere (submissions, evaluations, admin).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional

from pydantic import BeforeValidator, PlainSerializer

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - rare without tzdata
    IST = timezone(timedelta(hours=5, minutes=30))

UTC = timezone.utc


def now_ist() -> datetime:
    """Current time as a timezone-aware IST datetime."""
    return datetime.now(IST)


def now_ist_iso() -> str:
    """Current IST timestamp as ISO-8601 with ``+05:30`` offset."""
    return now_ist().isoformat()


def parse_to_ist(value: Any) -> datetime:
    """
    Parse a datetime / ISO string into IST.

    Naive values are treated as **UTC** (legacy ``datetime.utcnow()`` writes).
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Empty datetime string")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    else:
        raise TypeError(f"Unsupported datetime value: {type(value)!r}")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def to_ist_iso(value: Any | None) -> str | None:
    """Convert a datetime / ISO string to an IST ISO string (or None)."""
    if value is None or value == "":
        return None
    return parse_to_ist(value).isoformat()


def _validate_ist(value: Any) -> datetime:
    return parse_to_ist(value)


def _validate_optional_ist(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    return parse_to_ist(value)


def _serialize_ist(dt: datetime) -> str:
    return parse_to_ist(dt).isoformat()


def _serialize_optional_ist(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return parse_to_ist(dt).isoformat()


# Use on Pydantic response fields so API JSON always exposes IST (+05:30).
ISTDateTime = Annotated[
    datetime,
    BeforeValidator(_validate_ist),
    PlainSerializer(_serialize_ist, return_type=str),
]

OptionalISTDateTime = Annotated[
    Optional[datetime],
    BeforeValidator(_validate_optional_ist),
    PlainSerializer(_serialize_optional_ist, return_type=Optional[str]),
]
