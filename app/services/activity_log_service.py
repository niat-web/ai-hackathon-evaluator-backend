"""Who did what, plus the Super Admin observation snapshot.

Logs live in ``application_logs``. Charts are computed from those rows and
from the user roster. No extra package: the frontend draws the graphs.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from app.services.firebase import FirebaseService
from app.utils.ids import new_id
from app.utils.time import now_ist, parse_to_ist


logger = logging.getLogger(__name__)

COLLECTION = "application_logs"
RETENTION = timedelta(days=7)
ACTIVE_WINDOW = timedelta(minutes=15)
SERIES_HOURS = 24
POLL_AFTER_SECONDS = 15
_PRUNE_EVERY_SECONDS = 600
_last_prune = 0.0
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

_ROLE_LABELS = {
    "super_admin": "Super Admin",
    "admin": "Admin",
    "custom": "Custom",
    "evaluator": "Evaluator",
    "student": "Student",
}
_ROLE_ORDER = list(_ROLE_LABELS)
_WINDOWS = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}
_METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")
_OUTCOME_RANGES = {
    "success": (200, 299),
    "client_error": (400, 499),
    "server_error": (500, 599),
}
_OUTCOME_LABELS = {
    "success": "Success",
    "client_error": "Client error",
    "server_error": "Server error",
}

# Longest path prefix first. Method "*" matches every verb.
_OPERATIONS: tuple[tuple[str, str, str, str], ...] = (
    ("PATCH", "/auth/me", "Auth", "Updated profile"),
    ("POST", "/auth/login", "Auth", "Signed in"),
    ("POST", "/auth/logout", "Auth", "Signed out"),
    ("POST", "/auth/profile/start", "Auth", "Started profile verification"),
    ("POST", "/auth/profile/complete", "Auth", "Completed profile"),
    ("POST", "/auth/change-password", "Auth", "Changed password"),
    ("DELETE", "/admin/user/", "Students", "Deleted a student"),
    ("DELETE", "/admin/evaluators/", "Evaluators", "Deleted an evaluator"),
    ("POST", "/admin/evaluators/", "Evaluators", "Approved an evaluator"),
    ("POST", "/admin/access/users", "Access", "Created a staff user"),
    ("PATCH", "/admin/access/users/", "Access", "Updated a staff user"),
    ("POST", "/admin/access/roles", "Access", "Created a role"),
    ("PATCH", "/admin/access/roles/", "Access", "Updated a role"),
    ("DELETE", "/admin/access/roles/", "Access", "Deleted a role"),
    ("POST", "/submissions/", "Submissions", "Updated a submission"),
    ("GET", "/submissions", "Submissions", "Viewed submissions"),
    ("POST", "/hackathons", "Hackathons", "Created a hackathon"),
    ("PATCH", "/hackathons/", "Hackathons", "Updated a hackathon"),
    ("DELETE", "/hackathons/", "Hackathons", "Deleted a hackathon"),
    ("GET", "/hackathons", "Hackathons", "Viewed hackathons"),
    ("GET", "/admin/overview", "Overview", "Viewed overview"),
    ("*", "/admin/users", "Students", "Viewed students"),
    ("*", "/admin/evaluators", "Evaluators", "Viewed evaluators"),
    ("POST", "/super-admin/settings/reset-database", "Settings", "Reset the database"),
    (
        "POST",
        "/super-admin/settings/change-profile-password",
        "Settings",
        "Changed the profile password",
    ),
    ("*", "/super-admin/settings", "Settings", "Opened application settings"),
    ("*", "/themes", "Themes", "Worked on themes"),
    ("*", "/universities", "Universities", "Worked on universities"),
    ("*", "/evaluation-requirements", "Requirements", "Worked on requirements"),
    ("*", "/ai-evaluation-prompts", "Video Analysis", "Worked on video analysis"),
    ("*", "/admin/access", "Access", "Opened access"),
)

_SKIP_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/admin/access/logs",
    "/admin/access/observation",
    "/internal/",
)


def should_record(method: str, path: str) -> bool:
    """Skip health checks, session polls, and this page's own reads."""
    if method.upper() == "OPTIONS":
        return False
    clean = (path or "").split("?", 1)[0]
    if method.upper() == "GET" and clean in ("/auth/me", "/auth/csrf"):
        return False
    return not any(clean == prefix or clean.startswith(prefix) for prefix in _SKIP_PREFIXES)


def describe_operation(method: str, path: str) -> tuple[str, str]:
    """Area and a short sentence for one request."""
    verb = method.upper()
    clean = (path or "").split("?", 1)[0]
    for rule_method, prefix, area, label in _OPERATIONS:
        if rule_method not in ("*", verb):
            continue
        if clean == prefix or clean.startswith(prefix):
            return area, label
    return "Other", f"{verb} {clean}"


def _time_bounds(
    clock: datetime,
    window: str,
    from_time: str | None,
    to_time: str | None,
) -> tuple[datetime, datetime]:
    """Inclusive IST range. ``from`` / ``to`` replace the preset window."""
    if from_time or to_time:
        start = parse_to_ist(from_time) if from_time else clock - RETENTION
        end = parse_to_ist(to_time) if to_time else clock
    else:
        span = _WINDOWS.get((window or "").strip())
        if span is None:
            raise ValueError("Unknown window. Use 15m, 1h, 24h, or 7d.")
        start, end = clock - span, clock
    if start > end:
        raise ValueError("from must be before to")
    return start, end


def _outcome_of(status_code: int) -> str:
    if 200 <= status_code <= 299:
        return "success"
    if 400 <= status_code <= 499:
        return "client_error"
    if status_code >= 500:
        return "server_error"
    return ""


def _matches(
    row: dict[str, Any],
    *,
    needle: str,
    role: str,
    area: str,
    method: str,
    outcome: str,
    user_id: str,
    min_duration_ms: int,
) -> bool:
    if role and (row.get("role") or "") != role:
        return False
    if area and (row.get("area") or "") != area:
        return False
    if method and (row.get("method") or "").upper() != method:
        return False
    if user_id and (row.get("user_id") or "") != user_id:
        return False
    if outcome and _outcome_of(int(row.get("status_code") or 0)) != outcome:
        return False
    if min_duration_ms and int(row.get("duration_ms") or 0) < min_duration_ms:
        return False
    if not needle:
        return True
    haystack = " ".join(
        str(row.get(key) or "") for key in ("name", "email", "operation", "path", "area")
    )
    return needle in haystack.casefold()


def _facets(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Dropdown options counted before search and the other filters."""
    roles: Counter[str] = Counter()
    areas: Counter[str] = Counter()
    methods: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    for row in rows:
        if row.get("role"):
            roles[str(row["role"])] += 1
        if row.get("area"):
            areas[str(row["area"])] += 1
        if row.get("method"):
            methods[str(row["method"]).upper()] += 1
        outcomes[_outcome_of(int(row.get("status_code") or 0))] += 1

    role_keys = [key for key in _ROLE_ORDER if roles.get(key)]
    role_keys.extend(sorted(key for key in roles if key not in _ROLE_ORDER))
    return {
        "roles": [
            {"value": key, "label": _ROLE_LABELS.get(key, key), "count": roles[key]}
            for key in role_keys
        ],
        "areas": [{"value": key, "label": key, "count": areas[key]} for key in sorted(areas)],
        "methods": [
            {"value": key, "label": key, "count": methods[key]}
            for key in _METHODS
            if methods.get(key)
        ],
        "outcomes": [
            {"value": key, "label": label, "count": outcomes.get(key, 0)}
            for key, label in _OUTCOME_LABELS.items()
        ],
    }


class ActivityLogService:
    """Firestore-backed activity log and the observation snapshot."""

    def __init__(self, firebase: FirebaseService | None = None):
        self.firebase = firebase or FirebaseService()

    def record(self, entry: dict[str, Any]) -> None:
        """Store one action. Failures are logged and never raised to the request."""
        try:
            self.firebase.set_document(COLLECTION, new_id(), entry)
            self._maybe_prune()
        except Exception:
            logger.warning("Could not write an application log", exc_info=True)

    def list_logs(
        self,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        q: str = "",
        role: str = "",
        area: str = "",
        method: str = "",
        outcome: str = "",
        window: str = "7d",
        from_time: str | None = None,
        to_time: str | None = None,
        user_id: str = "",
        min_duration_ms: int = 0,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """One page of activity, plus filter options for that time window."""
        clock = now or now_ist()
        if clock.tzinfo is None:
            clock = parse_to_ist(clock)
        start, end = _time_bounds(clock, window, from_time, to_time)
        verb = (method or "").strip().upper()
        if verb and verb not in _METHODS:
            raise ValueError("Unknown method. Use GET, POST, PATCH, PUT, or DELETE.")
        outcome_key = (outcome or "").strip().lower()
        if outcome_key and outcome_key not in _OUTCOME_RANGES:
            raise ValueError("Unknown outcome. Use success, client_error, or server_error.")
        needle = (q or "").strip().casefold()
        role_key = (role or "").strip()
        area_key = (area or "").strip()
        actor_id = (user_id or "").strip()
        slow = max(int(min_duration_ms or 0), 0)

        windowed = [row for row in self._recent() if start <= self._at(row) <= end]
        filtered = [
            row
            for row in windowed
            if _matches(
                row,
                needle=needle,
                role=role_key,
                area=area_key,
                method=verb,
                outcome=outcome_key,
                user_id=actor_id,
                min_duration_ms=slow,
            )
        ]
        filtered.sort(
            key=lambda item: (item.get("created_at") or "", item.get("id") or ""),
            reverse=True,
        )

        page_size = min(max(int(page_size), 1), MAX_PAGE_SIZE)
        total = len(filtered)
        pages = max(1, (total + page_size - 1) // page_size) if total else 1
        page = min(max(int(page), 1), pages)
        start_index = (page - 1) * page_size
        durations = [int(row.get("duration_ms") or 0) for row in filtered]
        errors = sum(1 for row in filtered if int(row.get("status_code") or 0) >= 400)
        return {
            "items": filtered[start_index : start_index + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "summary": {
                "errors": errors,
                "average_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
            },
            "facets": _facets(windowed),
        }

    def observation(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Active-user series, role pie, operation pie, and speed for the last day."""
        clock = now or now_ist()
        if clock.tzinfo is None:
            clock = parse_to_ist(clock)
        logs = self._recent()
        day_start = clock - timedelta(hours=SERIES_HOURS)
        recent = [row for row in logs if self._at(row) >= day_start]
        active_cutoff = clock - ACTIVE_WINDOW
        active_ids = {
            row.get("user_id")
            for row in recent
            if row.get("user_id") and self._at(row) >= active_cutoff
        }

        hour_start = clock.replace(minute=0, second=0, microsecond=0)
        buckets = [
            hour_start - timedelta(hours=offset) for offset in range(SERIES_HOURS - 1, -1, -1)
        ]
        counts = {bucket: set() for bucket in buckets}
        for row in recent:
            stamp = self._at(row)
            bucket = stamp.replace(minute=0, second=0, microsecond=0)
            user_id = row.get("user_id")
            if user_id and bucket in counts:
                counts[bucket].add(user_id)

        durations = [int(row.get("duration_ms") or 0) for row in recent]
        errors = [row for row in recent if int(row.get("status_code") or 0) >= 400]
        areas = Counter(row.get("area") or "Other" for row in recent)
        roles = Counter(
            _ROLE_LABELS.get(user.get("role") or "", "Other")
            for user in self.firebase.get_collection("users")
        )

        request_count = len(recent)
        error_count = len(errors)
        return {
            "generated_at": clock.isoformat(),
            "poll_after_seconds": POLL_AFTER_SECONDS,
            "active_now": len(active_ids),
            "active_users_24h": [
                {
                    "at": bucket.isoformat(),
                    "label": bucket.strftime("%H:%M"),
                    "active_users": len(counts[bucket]),
                }
                for bucket in buckets
            ],
            "users_by_role": [
                {"label": label, "value": count} for label, count in sorted(roles.items()) if count
            ],
            "operations_by_area": [
                {"label": label, "value": count} for label, count in sorted(areas.items()) if count
            ],
            "requests_24h": request_count,
            "errors_24h": error_count,
            "error_rate": round(error_count / request_count, 4) if request_count else 0.0,
            "average_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
        }

    def _recent(self) -> list[dict[str, Any]]:
        cutoff = (now_ist() - RETENTION).isoformat()
        return [
            row
            for row in self.firebase.get_collection(COLLECTION)
            if (row.get("created_at") or "") >= cutoff
        ]

    def _maybe_prune(self) -> None:
        global _last_prune
        current = time.monotonic()
        if current - _last_prune < _PRUNE_EVERY_SECONDS:
            return
        _last_prune = current
        cutoff = (now_ist() - RETENTION).isoformat()
        for row in self.firebase.get_collection(COLLECTION):
            row_id = row.get("id")
            if row_id and (row.get("created_at") or "") < cutoff:
                self.firebase.delete_document(COLLECTION, row_id)

    @staticmethod
    def _at(row: dict[str, Any]) -> datetime:
        try:
            return parse_to_ist(row.get("created_at"))
        except (TypeError, ValueError):
            return parse_to_ist("1970-01-01T00:00:00+00:00")
