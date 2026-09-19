"""Per-round hackathon setting resolution."""

from app.utils.hackathon_round import (
    round_auto_ai_evaluation,
    round_working_demo_video_required,
    submission_auto_ai_enabled,
)


def test_round_settings_fallback_to_hackathon_legacy_fields():
    hackathon = {
        "working_demo_video_required": False,
        "auto_ai_evaluation": True,
        "timeline": [{"title": "Round 1"}],
    }
    assert round_working_demo_video_required(hackathon, 0) is False
    assert round_auto_ai_evaluation(hackathon, 0) is True


def test_round_settings_override_hackathon_legacy_fields():
    hackathon = {
        "working_demo_video_required": True,
        "auto_ai_evaluation": False,
        "timeline": [
            {
                "title": "Round 1",
                "working_demo_video_required": False,
                "auto_ai_evaluation": True,
            }
        ],
    }
    assert round_working_demo_video_required(hackathon, 0) is False
    assert round_auto_ai_evaluation(hackathon, 0) is True


def test_submission_auto_ai_uses_stored_snapshot():
    hackathon = {"auto_ai_evaluation": True, "timeline": []}
    assert submission_auto_ai_enabled(hackathon, {"auto_ai_evaluation": False}) is False


def test_submission_auto_ai_resolves_from_round_when_not_stored():
    hackathon = {
        "auto_ai_evaluation": False,
        "timeline": [{"title": "R1", "auto_ai_evaluation": True}],
    }
    assert submission_auto_ai_enabled(hackathon, {"round_index": 0}) is True


def test_round_github_ai_settings():
    hackathon = {
        "github_ai_evaluation": False,
        "timeline": [{"title": "R1", "github_ai_evaluation": True}],
    }
    from app.utils.hackathon_round import round_github_ai_evaluation

    assert round_github_ai_evaluation(hackathon, 0) is True
    assert round_github_ai_evaluation(hackathon, 99) is False
