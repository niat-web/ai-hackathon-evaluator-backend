"""One submission per student (and per team) per hackathon round."""

from __future__ import annotations

from typing import Any

from app.exceptions import ConflictError
from app.utils.hackathon_round import submission_round_index


SUBMISSIONS = "submissions"

ALREADY_SUBMITTED_MESSAGE = (
    "You have already submitted for this round. Only one submission is allowed per round."
)
TEAM_ALREADY_SUBMITTED_MESSAGE = (
    "Your team has already submitted for this round. Only one submission is allowed per round."
)


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


def find_existing_round_submission(
    firebase: Any,
    *,
    student_id: str,
    hackathon_id: str,
    round_index: int,
    team_id: str | None = None,
) -> dict[str, Any] | None:
    existing = find_student_round_submission(
        firebase,
        student_id=student_id,
        hackathon_id=hackathon_id,
        round_index=round_index,
    )
    if existing:
        return existing
    return find_team_round_submission(
        firebase,
        team_id=team_id,
        hackathon_id=hackathon_id,
        round_index=round_index,
    )


def assert_no_existing_round_submission(
    firebase: Any,
    *,
    student_id: str,
    hackathon_id: str,
    round_index: int,
    team_id: str | None = None,
) -> None:
    existing = find_student_round_submission(
        firebase,
        student_id=student_id,
        hackathon_id=hackathon_id,
        round_index=round_index,
    )
    if existing:
        raise ConflictError(ALREADY_SUBMITTED_MESSAGE, code="ALREADY_SUBMITTED")
    team_existing = find_team_round_submission(
        firebase,
        team_id=team_id,
        hackathon_id=hackathon_id,
        round_index=round_index,
    )
    if team_existing:
        raise ConflictError(TEAM_ALREADY_SUBMITTED_MESSAGE, code="ALREADY_SUBMITTED")
