"""IST start and end times for hackathon and round submission windows."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models.hackathon_model import HackathonCreateRequest, HackathonPrizes, TimelineRound
from app.utils.hackathon_round import round_open_for_submission, round_window_status
from app.utils.hackathon_schedule import window_status
from app.utils.time import IST


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 16, hour, minute, second, tzinfo=IST)


def test_missing_times_cover_the_whole_day():
    assert window_status("2026-10-16", "2026-10-16", None, None, now=_at(0, 0)) == "open"
    assert window_status("2026-10-16", "2026-10-16", None, None, now=_at(23, 59, 30)) == "open"
    assert window_status("2026-10-16", "2026-10-16", None, None, now=_at(23, 59, 59)) == "open"
    later = datetime(2026, 10, 17, 0, 0, tzinfo=IST)
    assert window_status("2026-10-16", "2026-10-16", None, None, now=later) == "closed"


def test_custom_times_open_and_close_on_the_minute():
    assert (
        window_status("2026-10-16", "2026-10-16", "10:00", "18:00", now=_at(9, 59)) == "scheduled"
    )
    assert window_status("2026-10-16", "2026-10-16", "10:00", "18:00", now=_at(10, 0)) == "open"
    assert window_status("2026-10-16", "2026-10-16", "10:00", "18:00", now=_at(18, 0, 30)) == "open"
    assert window_status("2026-10-16", "2026-10-16", "10:00", "18:00", now=_at(18, 1)) == "closed"


def test_round_without_a_stored_time_stays_open_during_the_day():
    round_ = {"start_date": "2026-10-16", "end_date": "2026-10-16", "published": True}
    assert round_window_status(round_, now=_at(15, 0)) == "open"
    hackathon = {"timeline": [round_]}
    assert round_open_for_submission(hackathon, 0, now=_at(15, 0)) is True
    early = {"start_date": "2026-10-16", "end_date": "2026-10-16", "start_time": "14:00"}
    assert round_window_status(early, now=_at(13, 59)) == "scheduled"


def test_same_day_end_before_start_is_rejected():
    with pytest.raises(ValidationError, match="end_time"):
        TimelineRound(
            title="Round 1",
            start_date="2026-10-16",
            end_date="2026-10-16",
            start_time="18:00",
            end_time="09:00",
        )


def test_create_defaults_times_when_omitted():
    payload = HackathonCreateRequest(
        name="Hack",
        description="Desc",
        start_date="2026-10-16",
        end_date="2026-10-18",
        guidelines="Do this",
        evaluator_guidelines="Score this",
        theme_ids=["theme-1"],
        prizes=HackathonPrizes(
            winner="A",
            first_runner_up="B",
            second_runner_up="C",
        ),
        timeline=[TimelineRound(title="Round 1", start_date="2026-10-16", end_date="2026-10-18")],
    )
    assert payload.start_time == "00:00"
    assert payload.end_time == "23:59"
    assert payload.timeline[0].start_time == "00:00"
    assert payload.timeline[0].end_time == "23:59"
