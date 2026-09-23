"""Submission cap per student (and per team) per hackathon round."""

from __future__ import annotations

from typing import Any

from app.exceptions import ConflictError
from app.utils.hackathon_round import normalize_max_submissions, submission_round_index


SUBMISSIONS = "submissions"

SUBMISSION_LIMIT_MESSAGE = "You cannot resubmit. You have exhausted the submission limit."
TEAM_SUBMISSION_LIMIT_MESSAGE = (
    "Your team cannot resubmit. You have exhausted the submission limit."
)

# Kept so older callers still import a message string.
ALREADY_SUBMITTED_MESSAGE = SUBMISSION_LIMIT_MESSAGE
TEAM_ALREADY_SUBMITTED_MESSAGE = TEAM_SUBMISSION_LIMIT_MESSAGE


def find_student_round_submission(
    firebase: Any,
    *,
    student_id: str,
    hackathon_id: str,
    round_index: int,
) -> dict[str, Any] | None:
    """Return this student's existing submission for the hackathon round, if any."""
    hid = hackathon_id.strip()
    uid = (student_id or "").strip()
    if not hid or not uid:
        return None
    docs = firebase.query_collection(SUBMISSIONS, "student_id", "==", uid)
    for doc in docs:
        if (doc.get("hackathon_id") or "").strip() != hid:
            continue
        if submission_round_index(doc) == round_index:
            return doc
    return None


def find_team_round_submission(
    firebase: Any,
    *,
    team_id: str | None,
    hackathon_id: str,
    round_index: int,
) -> dict[str, Any] | None:
    """Return the team's existing round submission (leader already submitted)."""
    tid = (team_id or "").strip()
    hid = hackathon_id.strip()
    if not tid or not hid:
        return None
    docs = firebase.query_collection(SUBMISSIONS, "hackathon_id", "==", hid)
    for doc in docs:
        if (doc.get("hackathon_team_id") or "").strip() != tid:
            continue
        if submission_round_index(doc) == round_index:
            return doc
    return None


def list_round_submissions(
    firebase: Any,
    *,
    student_id: str,
    hackathon_id: str,
    round_index: int,
    team_id: str | None = None,
) -> list[dict[str, Any]]:
    """Submissions already made for this student or team on this round."""
    found: dict[str, dict[str, Any]] = {}
    student_doc = find_student_round_submission(
        firebase,
        student_id=student_id,
        hackathon_id=hackathon_id,
        round_index=round_index,
    )
    # find_* returns only the first match. Collect every match below.
    hid = hackathon_id.strip()
    uid = (student_id or "").strip()
    if hid and uid:
        for doc in firebase.query_collection(SUBMISSIONS, "student_id", "==", uid):
            if (doc.get("hackathon_id") or "").strip() != hid:
                continue
            if submission_round_index(doc) == round_index and doc.get("id"):
                found[doc["id"]] = doc
    tid = (team_id or "").strip()
    if hid and tid:
        for doc in firebase.query_collection(SUBMISSIONS, "hackathon_id", "==", hid):
            if (doc.get("hackathon_team_id") or "").strip() != tid:
                continue
            if submission_round_index(doc) == round_index and doc.get("id"):
                found[doc["id"]] = doc
    if student_doc and student_doc.get("id") and student_doc["id"] not in found:
        found[student_doc["id"]] = student_doc
    items = list(found.values())
    items.sort(key=lambda doc: (str(doc.get("created_at") or ""), str(doc.get("id") or "")))
    return items


def find_existing_round_submission(
    firebase: Any,
    *,
    student_id: str,
    hackathon_id: str,
    round_index: int,
    team_id: str | None = None,
) -> dict[str, Any] | None:
    items = list_round_submissions(
        firebase,
        student_id=student_id,
        hackathon_id=hackathon_id,
        round_index=round_index,
        team_id=team_id,
    )
    return items[-1] if items else None


def assert_within_submission_limit(
    firebase: Any,
    *,
    student_id: str,
    hackathon_id: str,
    round_index: int,
    max_submissions: int,
    team_id: str | None = None,
) -> None:
    """Reject another submission once this round's cap is used up."""
    limit = normalize_max_submissions(max_submissions)
    items = list_round_submissions(
        firebase,
        student_id=student_id,
        hackathon_id=hackathon_id,
        round_index=round_index,
        team_id=team_id,
    )
    if len(items) < limit:
        return
    uid = (student_id or "").strip()
    student_has_one = any((doc.get("student_id") or "").strip() == uid for doc in items)
    if team_id and not student_has_one:
        raise ConflictError(TEAM_SUBMISSION_LIMIT_MESSAGE, code="SUBMISSION_LIMIT_REACHED")
    raise ConflictError(SUBMISSION_LIMIT_MESSAGE, code="SUBMISSION_LIMIT_REACHED")


def assert_no_existing_round_submission(
    firebase: Any,
    *,
    student_id: str,
    hackathon_id: str,
    round_index: int,
    team_id: str | None = None,
    max_submissions: int = 1,
) -> None:
    """Backward-compatible name. Default cap is 1."""
    assert_within_submission_limit(
        firebase,
        student_id=student_id,
        hackathon_id=hackathon_id,
        round_index=round_index,
        team_id=team_id,
        max_submissions=max_submissions,
    )
