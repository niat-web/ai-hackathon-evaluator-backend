"""
Shared fixtures for characterization / regression tests (Phase 0).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.models.user_model import CurrentUser


@pytest.fixture
def admin_user() -> CurrentUser:
    return CurrentUser(
        user_id="admin-1",
        email="admin@nxtwave.co.in",
        role="admin",
        name="Admin",
        approval_status="approved",
    )


@pytest.fixture
def student_user() -> CurrentUser:
    return CurrentUser(
        user_id="student-1",
        email="student@example.com",
        role="student",
        name="Student Team",
        approval_status="approved",
    )


@pytest.fixture
def evaluator_user() -> CurrentUser:
    return CurrentUser(
        user_id="evaluator-1",
        email="evaluator@nxtwave.co.in",
        role="evaluator",
        name="Evaluator One",
        approval_status="approved",
    )


@pytest.fixture
def pending_evaluator_user() -> CurrentUser:
    return CurrentUser(
        user_id="evaluator-pending",
        email="pending@nxtwave.co.in",
        role="evaluator",
        name="Pending Evaluator",
        approval_status="pending",
    )


def make_submission_doc(
    *,
    submission_id: str = "sub-1",
    student_id: str = "student-1",
    hackathon_id: str = "hack-1",
    status: str = "completed",
    assigned_evaluator_id: str | None = "evaluator-1",
    review_status: str = "none",
    analysis_id: str | None = "analysis-1",
    report_published: bool = False,
    final_score: float | None = None,
) -> dict[str, Any]:
    """Minimal submission document matching production field names."""
    now = datetime.utcnow().isoformat()
    return {
        "id": submission_id,
        "student_id": student_id,
        "hackathon_id": hackathon_id,
        "hackathon_name": "Idea2Impact",
        "team_name": "team peek",
        "theme_id": "theme-1",
        "theme_name": "Sustainability",
        "problem_statement": "Problem",
        "solution_description": "Solution",
        "evaluation_criteria": None,
        "status": status,
        "analysis_id": analysis_id,
        "report_published": report_published,
        "published_at": None,
        "published_by": None,
        "assigned_evaluator_id": assigned_evaluator_id,
        "assigned_evaluator_name": "Evaluator One" if assigned_evaluator_id else None,
        "assigned_at": now if assigned_evaluator_id else None,
        "assigned_by": "admin-1" if assigned_evaluator_id else None,
        "analyzed_by": None,
        "review_status": review_status,
        "final_score": final_score,
        "evaluator_notes": None,
        "submitted_for_review_at": None,
        "submitted_for_review_by": None,
        "reviewed_at": None,
        "reviewed_by": None,
        "review_notes": None,
        "video_path": f"gs://bucket/submissions/{student_id}/{submission_id}/video.webm",
        "content_type": "video/webm",
        "source_filename": "demo.webm",
        "video_source": "uploaded",
        "error": None,
        "created_at": now,
        "updated_at": now,
    }


@pytest.fixture
def mock_firebase() -> MagicMock:
    return MagicMock()
