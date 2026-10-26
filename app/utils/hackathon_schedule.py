"""IST date-and-time windows for a hackathon and its rounds.

A missing time means the whole calendar day: 00:00 through 23:59.
The chosen end minute is still open. 23:59 stays open until 23:59:59 IST.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from app.utils.time import IST

DEFAULT_START_TIME = "00:00"
DEFAULT_END_TIME = "23:59"
_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def normalize_clock(value: str | None, *, default: str) -> str:
    """Return HH:MM. Empty or invalid values use ``default``."""
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    if len(text) >= 8 and text[2] == ":" and text[5] == ":":
        text = text[:5]
    if not _CLOCK.match(text):
        raise ValueError("Time must be HH:MM in 24-hour IST, for example 09:30")
    return text


def stored_clock(value: str | None, *, default: str) -> str:
    """Clock for documents that predate times. Invalid stored values use the default."""
    try:
        return normalize_clock(value, default=default)
    except ValueError:
        return default


def _parse_date(value: str | None) -> date | None:
    if not value or not str(value).strip():
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def window_datetimes(
    start_date: str | None,
    end_date: str | None,
    start_time: str | None,
    end_time: str | None,
) -> tuple[datetime | None, datetime | None]:
    """Inclusive IST start, and the last inclusive second of the end minute."""
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    start_at = None
    end_at = None
    if start is not None:
        clock = stored_clock(start_time, default=DEFAULT_START_TIME)
        hour, minute = (int(part) for part in clock.split(":"))
        start_at = datetime(start.year, start.month, start.day, hour, minute, tzinfo=IST)
    if end is not None:
        clock = stored_clock(end_time, default=DEFAULT_END_TIME)
        hour, minute = (int(part) for part in clock.split(":"))
        end_at = datetime(end.year, end.month, end.day, hour, minute, 59, tzinfo=IST)
    return start_at, end_at


def assert_window_order(
    start_date: str | None,
    end_date: str | None,
    start_time: str | None,
    end_time: str | None,
) -> None:
    """Reject an end that is earlier than the start. Equal minute is allowed."""
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start is None or end is None:
        return
    if end < start:
        raise ValueError("end_date cannot be earlier than start_date")
    if end == start:
        start_clock = normalize_clock(start_time, default=DEFAULT_START_TIME)
        end_clock = normalize_clock(end_time, default=DEFAULT_END_TIME)
        if end_clock < start_clock:
            raise ValueError("end_time cannot be earlier than start_time")


def window_status(
    start_date: str | None,
    end_date: str | None,
    start_time: str | None,
    end_time: str | None,
    *,
    now: datetime,
) -> str:
    """``scheduled`` before the start, ``closed`` after the end minute, else ``open``."""
    current = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    start_at, end_at = window_datetimes(start_date, end_date, start_time, end_time)
    if end_at is not None and current > end_at:
        return "closed"
    if start_at is not None and current < start_at:
        return "scheduled"
    return "open"


def clock_label(value: str | None, *, default: str) -> str:
    """12-hour label such as ``12:00 AM`` or ``11:59 PM``."""
    clock = stored_clock(value, default=default)
    hour, minute = (int(part) for part in clock.split(":"))
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"


def opens_on_message(start_date: str | None, start_time: str | None, *, what: str) -> str:
    day = str(start_date or "").strip()
    label = clock_label(start_time, default=DEFAULT_START_TIME)
    if day:
        return f"{what} opens on {day} at {label} IST."
    return f"{what} is not open yet."


def closed_on_message(end_date: str | None, end_time: str | None, *, what: str) -> str:
    day = str(end_date or "").strip()
    label = clock_label(end_time, default=DEFAULT_END_TIME)
    if day:
        return f"{what} closed on {day} at {label} IST."
    return f"{what} has closed."


def apply_window_times(data: dict) -> dict:
    """Fill missing times so API clients always see a clock."""
    data["start_time"] = stored_clock(data.get("start_time"), default=DEFAULT_START_TIME)
    data["end_time"] = stored_clock(data.get("end_time"), default=DEFAULT_END_TIME)
    return data
