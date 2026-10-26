"""Who may see a hackathon on the student dashboard and submit to it."""

from __future__ import annotations

from typing import Any


def _id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def normalize_visibility(hackathon: dict[str, Any] | None) -> dict[str, Any]:
    """
    Stored rule, with a default of every university and every batch.

    Hackathons saved before this setting existed stay visible to every student.
    """
    raw = (hackathon or {}).get("visibility")
    if not isinstance(raw, dict):
        return {
            "all_universities": True,
            "all_batches": True,
            "university_ids": [],
            "batch_ids": [],
        }
    all_universities = raw.get("all_universities")
    all_batches = raw.get("all_batches")
    universities_open = True if all_universities is None else bool(all_universities)
    batches_open = True if all_batches is None else bool(all_batches)
    return {
        "all_universities": universities_open,
        "all_batches": batches_open,
        "university_ids": [] if universities_open else _id_list(raw.get("university_ids")),
        "batch_ids": [] if batches_open else _id_list(raw.get("batch_ids")),
    }


def student_can_see(
    hackathon: dict[str, Any] | None,
    university_id: str | None,
    batch_id: str | None,
) -> bool:
    """True when this student's university and batch are inside the rule."""
    rule = normalize_visibility(hackathon)
    if rule["all_universities"] and rule["all_batches"]:
        return True
    university = str(university_id or "").strip()
    batch = str(batch_id or "").strip()
    university_ok = rule["all_universities"] or university in rule["university_ids"]
    batch_ok = rule["all_batches"] or batch in rule["batch_ids"]
    return university_ok and batch_ok


def visible_to_viewer(hackathon: dict[str, Any] | None, viewer: Any | None) -> bool:
    """
    Anonymous visitors only see a hackathon opened to every university and batch.

    Students see it when their profile matches. Staff always see it.
    """
    if viewer is None:
        rule = normalize_visibility(hackathon)
        return bool(rule["all_universities"] and rule["all_batches"])
    if getattr(viewer, "role", None) != "student":
        return True
    return student_can_see(
        hackathon,
        getattr(viewer, "university_id", None),
        getattr(viewer, "batch_id", None),
    )
