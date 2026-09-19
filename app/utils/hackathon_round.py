"""Resolve per-round hackathon settings with legacy hackathon-level fallbacks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from app.exceptions import BadRequestError
from app.utils.time import now_ist


TEAM_MODE_LABELS = {
    1: "Solo",
    2: "2 Members",
    3: "3 Members",
    4: "4 Members",
}

RoundStatus = Literal["draft", "scheduled", "open", "closed"]
CatalogStatus = Literal["upcoming", "open", "closing_soon", "closed"]

TEAM_INCOMPLETE_MESSAGE = "Please complete your team to move to demo video"
CLOSING_SOON_DAYS = 3
CATALOG_STATUS_LABELS = {
    "upcoming": "Upcoming",
    "open": "Open",
    "closing_soon": "Closing soon",
    "closed": "Closed",
}


def normalize_max_team_size(value: Any) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = 1
    return max(1, min(4, size))


def hackathon_default_video_required(hackathon: dict[str, Any]) -> bool:
    """Legacy hackathon-level default (true when unset)."""
    return bool(hackathon.get("working_demo_video_required", True))


def hackathon_default_auto_ai(hackathon: dict[str, Any]) -> bool:
    """Legacy hackathon-level default (false when unset)."""
    return bool(hackathon.get("auto_ai_evaluation", False))


def hackathon_default_github_ai(hackathon: dict[str, Any]) -> bool:
    """Legacy hackathon-level default (false when unset)."""
    return bool(hackathon.get("github_ai_evaluation", False))


def get_timeline_round(
    hackathon: dict[str, Any], round_index: int
) -> dict[str, Any] | None:
    timeline = hackathon.get("timeline") or []
    if round_index < 0 or round_index >= len(timeline):
        return None
    round_ = timeline[round_index]
    return dict(round_) if isinstance(round_, dict) else round_.model_dump()


def round_title(hackathon: dict[str, Any], round_index: int) -> str:
    round_ = get_timeline_round(hackathon, round_index)
    if not round_:
        return f"Round {round_index + 1}"
    return str(round_.get("title") or f"Round {round_index + 1}")


def round_working_demo_video_required(
    hackathon: dict[str, Any], round_index: int
) -> bool:
    round_ = get_timeline_round(hackathon, round_index)
    if round_ is None:
        return hackathon_default_video_required(hackathon)
    if "working_demo_video_required" in round_:
        return bool(round_["working_demo_video_required"])
    return hackathon_default_video_required(hackathon)


def round_auto_ai_evaluation(hackathon: dict[str, Any], round_index: int) -> bool:
    round_ = get_timeline_round(hackathon, round_index)
    if round_ is None:
        return hackathon_default_auto_ai(hackathon)
    if "auto_ai_evaluation" in round_:
        return bool(round_["auto_ai_evaluation"])
    return hackathon_default_auto_ai(hackathon)


def round_github_ai_evaluation(hackathon: dict[str, Any], round_index: int) -> bool:
    round_ = get_timeline_round(hackathon, round_index)
    if round_ is None:
        return hackathon_default_github_ai(hackathon)
    if "github_ai_evaluation" in round_:
        return bool(round_["github_ai_evaluation"])
    return hackathon_default_github_ai(hackathon)


def round_leaderboard_published(hackathon: dict[str, Any], round_index: int) -> bool:
    """True when students may view this round's ranked results."""
    round_ = get_timeline_round(hackathon, round_index)
    if round_ is None:
        return False
    return bool(round_.get("leaderboard_published"))


def submission_round_index(submission: dict[str, Any]) -> int:
    try:
        return max(0, int(submission.get("round_index", 0)))
    except (TypeError, ValueError):
        return 0


def submission_auto_ai_enabled(
    hackathon: dict[str, Any] | None, submission: dict[str, Any]
) -> bool:
    if not hackathon:
        return False
    if submission.get("auto_ai_evaluation") is not None:
        return bool(submission["auto_ai_evaluation"])
    return round_auto_ai_evaluation(hackathon, submission_round_index(submission))


def submission_github_ai_enabled(
    hackathon: dict[str, Any] | None, submission: dict[str, Any]
) -> bool:
    if not hackathon:
        return False
    if submission.get("github_ai_evaluation") is not None:
        return bool(submission["github_ai_evaluation"])
    return round_github_ai_evaluation(hackathon, submission_round_index(submission))


def parse_iso_date(value: str | None) -> date | None:
    if not value or not str(value).strip():
        return None
    return date.fromisoformat(str(value).strip())


def round_is_published(round_: dict[str, Any]) -> bool:
    return bool(round_.get("published"))


def round_window_status(
    round_: dict[str, Any],
    *,
    now: datetime,
) -> RoundStatus:
    """IST calendar-day window for a round (independent of publish flag)."""
    today = now.astimezone(now.tzinfo).date()
    start = parse_iso_date(round_.get("start_date"))
    end = parse_iso_date(round_.get("end_date"))
    if end and today > end:
        return "closed"
    if start and today < start:
        return "scheduled"
    return "open"


def round_student_status(
    round_: dict[str, Any],
    *,
    now: datetime,
) -> RoundStatus:
    """Effective status for students: unpublished rounds are ``draft``."""
    if not round_is_published(round_):
        return "draft"
    return round_window_status(round_, now=now)


def validate_round_publishable(round_: dict[str, Any], *, now: datetime) -> None:
    """
    Validate admin can publish this round (IST calendar dates).

    Blocks when the round end date has already passed.
    """
    today = now.date()
    start = parse_iso_date(round_.get("start_date"))
    end = parse_iso_date(round_.get("end_date"))
    if start and end and end < start:
        raise BadRequestError(
            "Round end_date cannot be earlier than start_date",
            code="INVALID_ROUND_DATES",
        )
    if end and today > end:
        raise BadRequestError(
            "Cannot publish this round: the end date has already passed (IST).",
            code="ROUND_ENDED",
        )


def round_open_for_submission(
    hackathon: dict[str, Any],
    round_index: int,
    *,
    now: datetime,
) -> bool:
    round_ = get_timeline_round(hackathon, round_index)
    if not round_ or not round_is_published(round_):
        return False
    return round_window_status(round_, now=now) == "open"


def enrich_timeline_round(
    round_: dict[str, Any],
    *,
    hackathon: dict[str, Any],
) -> dict[str, Any]:
    """Normalize one timeline round for API responses."""
    data = dict(round_)
    max_size = normalize_max_team_size(data.get("max_team_size", 1))
    data["max_team_size"] = max_size
    data["team_mode_label"] = TEAM_MODE_LABELS.get(max_size, f"{max_size} Members")
    if "working_demo_video_required" not in data:
        data["working_demo_video_required"] = hackathon_default_video_required(hackathon)
    else:
        data["working_demo_video_required"] = bool(data["working_demo_video_required"])
    if "auto_ai_evaluation" not in data:
        data["auto_ai_evaluation"] = hackathon_default_auto_ai(hackathon)
    else:
        data["auto_ai_evaluation"] = bool(data["auto_ai_evaluation"])
    if "github_ai_evaluation" not in data:
        data["github_ai_evaluation"] = hackathon_default_github_ai(hackathon)
    else:
        data["github_ai_evaluation"] = bool(data["github_ai_evaluation"])
    data.setdefault("published", False)
    data["published"] = bool(data.get("published"))
    data.setdefault("leaderboard_published", False)
    data["leaderboard_published"] = bool(data.get("leaderboard_published"))
    status = round_student_status(data, now=now_ist())
    data["round_status"] = status
    if not data["published"]:
        data["published_at"] = None
        data["published_by"] = None
    if not data["leaderboard_published"]:
        data["leaderboard_published_at"] = None
        data["leaderboard_published_by"] = None
    return data


def pick_featured_published_round(
    timeline: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]] | None:
    """
    Homepage card uses one published round: first open, else first scheduled,
    else the last closed round. Unpublished rounds are ignored.
    """
    published: list[tuple[int, dict[str, Any]]] = []
    for index, round_ in enumerate(timeline or []):
        data = dict(round_) if isinstance(round_, dict) else {}
        if not data.get("published"):
            continue
        published.append((index, data))
    if not published:
        return None
    open_rounds = [
        item for item in published if item[1].get("round_status") == "open"
    ]
    if open_rounds:
        return open_rounds[0]
    scheduled = [
        item for item in published if item[1].get("round_status") == "scheduled"
    ]
    if scheduled:
        return scheduled[0]
    return published[-1]


def catalog_status_for_round(
    round_: dict[str, Any],
    *,
    now: datetime,
    closing_soon_days: int = CLOSING_SOON_DAYS,
) -> CatalogStatus:
    """Map a published round to homepage status (IST calendar days)."""
    student_status = round_.get("round_status") or round_student_status(
        round_, now=now
    )
    if student_status == "closed":
        return "closed"
    if student_status != "open":
        return "upcoming"
    today = now.date()
    end = parse_iso_date(round_.get("end_date"))
    if end is not None:
        days_left = (end - today).days
        if 0 <= days_left <= closing_soon_days:
            return "closing_soon"
    return "open"


def catalog_team_mode(max_team_size: Any) -> tuple[str, str]:
    size = normalize_max_team_size(max_team_size)
    if size <= 1:
        return "solo", "Solo"
    return "team", "Team"


_CATALOG_SORT = {
    "closing_soon": 0,
    "open": 1,
    "upcoming": 2,
    "closed": 3,
}


def catalog_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    status = str(item.get("status") or "closed")
    return (
        _CATALOG_SORT.get(status, 9),
        str(item.get("end_date") or "9999-12-31"),
        str(item.get("name") or ""),
    )
