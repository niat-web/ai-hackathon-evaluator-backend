"""Resolve per-round hackathon settings with legacy hackathon-level fallbacks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from app.exceptions import BadRequestError
from app.utils.hackathon_schedule import apply_window_times, assert_window_order, window_status
from app.utils.time import now_ist


MAX_ROUND_TEAM_SIZE = 5
MIN_FLEX_TEAM_SIZE = 2
MIN_MULTI_TEAM_SIZE = 2
MAX_MULTI_TEAM_SIZE = 6
DEFAULT_MAX_SUBMISSIONS = 1
MAX_SUBMISSIONS_PER_ROUND = 3

TEAM_MODE_LABELS = {
    1: "Solo",
    5: "2-5 Members",
}
TEAM_SIZE_MODES = ("single", "multi", "open")

RoundStatus = Literal["draft", "scheduled", "open", "closed"]
CatalogStatus = Literal["upcoming", "open", "closing_soon", "closed"]

TEAM_INCOMPLETE_MESSAGE = (
    "Add at least one teammate. A team must have 2 to 5 members before you can submit."
)
CLOSING_SOON_DAYS = 3
CATALOG_STATUS_LABELS = {
    "upcoming": "Upcoming",
    "open": "Open",
    "closing_soon": "Closing soon",
    "closed": "Closed",
}


def normalize_max_team_size(value: Any) -> int:
    """Solo stays 1. Any team size is the flexible 2–5 member round."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = 1
    if size <= 1:
        return 1
    return MAX_ROUND_TEAM_SIZE


def min_team_size_for(max_team_size: Any) -> int:
    """Smallest roster that may submit: 1 for solo, 2 for a legacy team round."""
    if normalize_max_team_size(max_team_size) <= 1:
        return 1
    return MIN_FLEX_TEAM_SIZE


def team_incomplete_message(min_size: int, max_size: int | None) -> str:
    """Why a leader cannot submit yet for this round's size rule."""
    if min_size == MIN_FLEX_TEAM_SIZE and max_size == MAX_ROUND_TEAM_SIZE:
        return TEAM_INCOMPLETE_MESSAGE
    if max_size is not None and min_size == max_size:
        return f"This team must have exactly {min_size} members before you can submit."
    if max_size is None:
        return "Create the team before you can submit."
    return f"This team must have {min_size} to {max_size} members before you can submit."


def _multi_label(min_size: int, max_size: int) -> str:
    if min_size == MIN_FLEX_TEAM_SIZE and max_size == MAX_ROUND_TEAM_SIZE:
        return TEAM_MODE_LABELS[MAX_ROUND_TEAM_SIZE]
    if min_size == max_size:
        return f"{min_size} members"
    return f"{min_size}-{max_size} members"


def team_too_large_message(max_size: int) -> str:
    return f"A team cannot have more than {max_size} members."


@dataclass(frozen=True)
class TeamBounds:
    mode: str
    min_size: int
    max_size: int | None
    label: str


def resolve_team_bounds(data: dict[str, Any] | None) -> TeamBounds:
    """
    Return ``(team_size_mode, min_size, max_size, label)``.

    ``max_size`` is ``None`` when the round has no fixed team size.
    Rounds saved before this field existed stay Solo (1) or a flexible 2–5 team.
    """
    source = data or {}
    mode = source.get("team_size_mode")
    if mode == "single":
        return TeamBounds("single", 1, 1, "Single person")
    if mode == "open":
        return TeamBounds("open", 1, None, "No fixed team size")
    if mode == "multi":
        try:
            min_size = int(source.get("min_team_size") or MIN_MULTI_TEAM_SIZE)
        except (TypeError, ValueError):
            min_size = MIN_MULTI_TEAM_SIZE
        try:
            max_size = int(source.get("max_team_size") or min_size)
        except (TypeError, ValueError):
            max_size = min_size
        min_size = max(MIN_MULTI_TEAM_SIZE, min(MAX_MULTI_TEAM_SIZE, min_size))
        max_size = max(min_size, min(MAX_MULTI_TEAM_SIZE, max_size))
        return TeamBounds("multi", min_size, max_size, _multi_label(min_size, max_size))

    max_size = normalize_max_team_size(source.get("max_team_size", 1))
    if max_size <= 1:
        return TeamBounds("single", 1, 1, TEAM_MODE_LABELS[1])
    return TeamBounds(
        "multi",
        MIN_FLEX_TEAM_SIZE,
        max_size,
        TEAM_MODE_LABELS.get(max_size, "2-5 Members"),
    )


def validate_team_size_fields(
    mode: str | None,
    min_team_size: int | None,
    max_team_size: int | None,
) -> TeamBounds:
    """Validate a round write. Missing mode keeps the legacy Solo / 2–5 rule."""
    if mode is None:
        if max_team_size is not None and max_team_size > MAX_ROUND_TEAM_SIZE:
            raise ValueError("max_team_size cannot be greater than 5 unless team_size_mode is set")
        stored = max_team_size if max_team_size is not None else 1
        return resolve_team_bounds({"max_team_size": stored})
    if mode not in TEAM_SIZE_MODES:
        raise ValueError("team_size_mode must be single, multi, or open")
    if mode == "single":
        return TeamBounds("single", 1, 1, "Single person")
    if mode == "open":
        return TeamBounds("open", 1, None, "No fixed team size")
    if min_team_size is None or max_team_size is None:
        raise ValueError("Minimum and maximum participants are required")
    if not MIN_MULTI_TEAM_SIZE <= min_team_size <= MAX_MULTI_TEAM_SIZE:
        raise ValueError("Minimum participants must be from 2 to 6")
    if not MIN_MULTI_TEAM_SIZE <= max_team_size <= MAX_MULTI_TEAM_SIZE:
        raise ValueError("Maximum participants must be from 2 to 6")
    if min_team_size > max_team_size:
        raise ValueError("Minimum participants cannot be greater than the maximum")
    return TeamBounds(
        "multi", min_team_size, max_team_size, _multi_label(min_team_size, max_team_size)
    )


def normalize_max_submissions(value: Any) -> int:
    """How many times a student or team may submit for one round. Default 1, max 3."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = DEFAULT_MAX_SUBMISSIONS
    return max(1, min(MAX_SUBMISSIONS_PER_ROUND, size))


def hackathon_max_submissions(hackathon: dict[str, Any] | None) -> int:
    if not hackathon:
        return DEFAULT_MAX_SUBMISSIONS
    return normalize_max_submissions(hackathon.get("max_submissions", DEFAULT_MAX_SUBMISSIONS))


def hackathon_default_video_required(hackathon: dict[str, Any]) -> bool:
    """Legacy hackathon-level default (true when unset)."""
    return bool(hackathon.get("working_demo_video_required", True))


def hackathon_default_auto_ai(hackathon: dict[str, Any]) -> bool:
    """Legacy hackathon-level default (false when unset)."""
    return bool(hackathon.get("auto_ai_evaluation", False))


def hackathon_default_github_ai(hackathon: dict[str, Any]) -> bool:
    """Legacy hackathon-level default (false when unset)."""
    return bool(hackathon.get("github_ai_evaluation", False))


def get_timeline_round(hackathon: dict[str, Any], round_index: int) -> dict[str, Any] | None:
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


def round_working_demo_video_required(hackathon: dict[str, Any], round_index: int) -> bool:
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
    """IST date-and-time window for a round (independent of publish flag)."""
    status = window_status(
        round_.get("start_date"),
        round_.get("end_date"),
        round_.get("start_time"),
        round_.get("end_time"),
        now=now,
    )
    if status == "closed":
        return "closed"
    if status == "scheduled":
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
    Validate admin can publish this round (IST date and time).

    Blocks when the round end minute has already passed.
    """
    try:
        assert_window_order(
            round_.get("start_date"),
            round_.get("end_date"),
            round_.get("start_time"),
            round_.get("end_time"),
        )
    except ValueError as exc:
        raise BadRequestError(str(exc), code="INVALID_ROUND_DATES") from exc
    if round_window_status(round_, now=now) == "closed":
        raise BadRequestError(
            "Cannot publish this round: the end time has already passed (IST).",
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
    apply_window_times(data)
    bounds = resolve_team_bounds(data)
    mode, min_size, max_size, label = (
        bounds.mode,
        bounds.min_size,
        bounds.max_size,
        bounds.label,
    )
    data["team_size_mode"] = mode
    data["min_team_size"] = min_size
    data["max_team_size"] = max_size
    data["team_mode_label"] = label
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
    open_rounds = [item for item in published if item[1].get("round_status") == "open"]
    if open_rounds:
        return open_rounds[0]
    scheduled = [item for item in published if item[1].get("round_status") == "scheduled"]
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
    student_status = round_.get("round_status") or round_student_status(round_, now=now)
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
    """Legacy helper: 1 is Solo, any larger stored size is Team."""
    size = normalize_max_team_size(max_team_size)
    if size <= 1:
        return "solo", "Solo"
    return "team", "Team"


def catalog_team_mode_for_round(round_: dict[str, Any] | None) -> tuple[str, str]:
    """Homepage chip: Solo, Team, or No fixed team size."""
    mode = resolve_team_bounds(round_).mode
    if mode == "single":
        return "solo", "Solo"
    if mode == "open":
        return "open", "No fixed team size"
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
