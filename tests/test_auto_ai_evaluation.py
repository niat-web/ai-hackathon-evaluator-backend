"""Tests for hackathon auto AI evaluation mode."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.auto_ai_evaluation import (
    hackathon_auto_ai_enabled,
    should_queue_auto_evaluation,
)


def test_hackathon_auto_ai_defaults_off():
    assert hackathon_auto_ai_enabled(None) is False
    assert hackathon_auto_ai_enabled({}) is False
    assert hackathon_auto_ai_enabled({"auto_ai_evaluation": False}) is False
    assert hackathon_auto_ai_enabled({"auto_ai_evaluation": True}) is True


def test_should_queue_requires_assignee_and_idle_status():
    assert should_queue_auto_evaluation({}) is False
    assert (
        should_queue_auto_evaluation(
            {"assigned_evaluator_id": "e1", "status": "uploaded"}
        )
        is True
    )
    assert (
        should_queue_auto_evaluation(
            {"assigned_evaluator_id": "e1", "status": "failed"}
        )
        is True
    )
    assert (
        should_queue_auto_evaluation(
            {"assigned_evaluator_id": "e1", "status": "processing"}
        )
        is False
    )
    assert (
        should_queue_auto_evaluation(
            {"assigned_evaluator_id": "e1", "status": "completed"}
        )
        is False
    )


@pytest.mark.asyncio
async def test_queue_auto_ai_skips_when_flag_off():
    from app.services.auto_ai_evaluation import queue_auto_ai_evaluations

    service = MagicMock()
    job_service = MagicMock()
    bg = MagicMock()
    queued = await queue_auto_ai_evaluations(
        service=service,
        job_service=job_service,
        background_tasks=bg,
        submissions=[
            {
                "id": "s1",
                "assigned_evaluator_id": "e1",
                "status": "uploaded",
                "hackathon_id": "h1",
            }
        ],
        analyzed_by="admin-1",
        hackathon={"id": "h1", "auto_ai_evaluation": False},
    )
    assert queued == 0
    service.mark_queued_for_evaluation.assert_not_called()


def test_hackathon_auto_ai_true_when_any_round_enabled():
    assert hackathon_auto_ai_enabled({"timeline": [{"auto_ai_evaluation": True}]}) is True


@pytest.mark.asyncio
async def test_queue_auto_ai_uses_round_flag_on_submission():
    from app.services.auto_ai_evaluation import queue_auto_ai_evaluations

    service = MagicMock()
    job_service = MagicMock()
    job_service.resolve_mode.return_value = "background"
    bg = MagicMock()

    with patch(
        "app.services.auto_ai_evaluation.run_sync",
        new_callable=AsyncMock,
    ) as run_sync:
        run_sync.return_value = "analysis-1"
        queued = await queue_auto_ai_evaluations(
            service=service,
            job_service=job_service,
            background_tasks=bg,
            submissions=[
                {
                    "id": "s1",
                    "assigned_evaluator_id": "e1",
                    "status": "uploaded",
                    "hackathon_id": "h1",
                    "round_index": 0,
                    "auto_ai_evaluation": True,
                }
            ],
            analyzed_by="admin-1",
            hackathon={
                "id": "h1",
                "auto_ai_evaluation": False,
                "timeline": [{"title": "R1", "auto_ai_evaluation": True}],
            },
        )

    assert queued == 1
    job_service.enqueue_background.assert_called_once_with("s1", None, bg)


@pytest.mark.asyncio
async def test_queue_auto_ai_enqueues_when_hackathon_flag_on():
    from app.services.auto_ai_evaluation import queue_auto_ai_evaluations

    service = MagicMock()
    job_service = MagicMock()
    job_service.resolve_mode.return_value = "background"
    bg = MagicMock()

    with patch(
        "app.services.auto_ai_evaluation.run_sync",
        new_callable=AsyncMock,
    ) as run_sync:
        run_sync.return_value = "analysis-1"
        queued = await queue_auto_ai_evaluations(
            service=service,
            job_service=job_service,
            background_tasks=bg,
            submissions=[
                {
                    "id": "s1",
                    "assigned_evaluator_id": "e1",
                    "status": "uploaded",
                    "hackathon_id": "h1",
                }
            ],
            analyzed_by="admin-1",
            hackathon={"id": "h1", "auto_ai_evaluation": True},
        )

    assert queued == 1
    job_service.enqueue_background.assert_called_once_with("s1", None, bg)


def test_show_ai_evaluation_button_logic_via_enrich():
    from app.models.user_model import CurrentUser
    from app.services.submission.query import QueryMixin

    class Host(QueryMixin):
        def __init__(self):
            self.hackathon_service = MagicMock()
            self.theme_service = MagicMock()
            self.user_service = MagicMock()
            self.firebase = MagicMock()
            self.analysis_collection = "analysis"

        def student_can_view_report(self, submission):
            return False

        def _storage_client(self):
            return MagicMock()

    host = Host()
    host.hackathon_service.get_hackathon.return_value = {
        "id": "h1",
        "name": "Hack",
        "auto_ai_evaluation": False,
    }
    evaluator = CurrentUser(
        user_id="e1",
        email="e@x.com",
        role="evaluator",
        name="E",
        approval_status="approved",
    )
    enriched = host.enrich_submission_for_response(
        {
            "id": "s1",
            "student_id": "stu",
            "hackathon_id": "h1",
            "hackathon_name": "Hack",
            "team_name": "T",
            "theme_id": "t1",
            "theme_name": "Theme",
            "problem_statement": "p",
            "solution_description": "s",
            "status": "uploaded",
            "assigned_evaluator_id": "e1",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        },
        current_user=evaluator,
    )
    assert enriched["auto_ai_evaluation"] is False
    assert enriched["show_ai_evaluation_button"] is True

    host.hackathon_service.get_hackathon.return_value["auto_ai_evaluation"] = True
    enriched_auto = host.enrich_submission_for_response(
        {
            "id": "s1",
            "student_id": "stu",
            "hackathon_id": "h1",
            "hackathon_name": "Hack",
            "team_name": "T",
            "theme_id": "t1",
            "theme_name": "Theme",
            "problem_statement": "p",
            "solution_description": "s",
            "status": "uploaded",
            "assigned_evaluator_id": "e1",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        },
        current_user=evaluator,
    )
    assert enriched_auto["auto_ai_evaluation"] is True
    assert enriched_auto["show_ai_evaluation_button"] is False
