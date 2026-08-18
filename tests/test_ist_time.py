"""IST timestamp helpers used across API responses."""

from datetime import datetime, timezone

from app.utils.time import now_ist_iso, parse_to_ist, to_ist_iso
from app.models.submission_model import SubmissionResponse


def test_now_ist_iso_includes_offset():
    stamp = now_ist_iso()
    assert "+05:30" in stamp


def test_naive_utc_legacy_converts_to_ist():
    # 12:00 UTC → 17:30 IST
    ist = parse_to_ist("2026-08-05T12:00:00")
    assert ist.hour == 17
    assert ist.minute == 30
    assert ist.utcoffset().total_seconds() == 5.5 * 3600


def test_aware_utc_converts_to_ist():
    utc = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
    assert to_ist_iso(utc) == "2026-08-05T05:30:00+05:30"


def test_submission_response_serializes_created_at_as_ist():
    payload = SubmissionResponse(
        id="s1",
        student_id="u1",
        hackathon_id="h1",
        hackathon_name="Hack",
        team_name="Team",
        theme_id="t1",
        theme_name="Theme",
        problem_statement="P",
        solution_description="S",
        status="uploaded",
        created_at="2026-08-05T12:00:00",  # legacy naive UTC
        updated_at="2026-08-05T12:00:00Z",
    )
    data = payload.model_dump(mode="json")
    assert data["created_at"].endswith("+05:30")
    assert data["created_at"].startswith("2026-08-05T17:30:00")
