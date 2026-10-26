"""Super Admin bulk-approves evaluator-submitted reviews for one hackathon."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.submission_service import SubmissionService


@pytest.fixture
def service() -> SubmissionService:
    with patch.object(SubmissionService, "__init__", lambda self: None):
        svc = SubmissionService()
        svc.collection = "submissions"
        svc.analysis_collection = "analysis"
        svc.firebase = MagicMock()
        svc.hackathon_service = MagicMock()
        svc.hackathon_service.get_hackathon.return_value = {"id": "hack-1"}
        return svc


def test_summary_counts_pending_approved_and_the_rest(service: SubmissionService):
    service.firebase.query_collection.return_value = [
        {"id": "ready", "status": "completed", "review_status": "pending_review"},
        {"id": "done", "status": "completed", "review_status": "approved"},
        {"id": "draft", "status": "uploaded", "review_status": "none"},
        {"id": "changes", "status": "completed", "review_status": "changes_requested"},
    ]

    summary = service.evaluation_approval_summary("hack-1")

    assert summary["pending_review_count"] == 1
    assert summary["approved_count"] == 1
    assert summary["skipped_count"] == 2


def test_bulk_approve_only_calls_pending_reviews(service: SubmissionService):
    service.firebase.query_collection.return_value = [
        {"id": "ready", "status": "completed", "review_status": "pending_review"},
        {"id": "done", "status": "completed", "review_status": "approved"},
        {"id": "waiting", "status": "processing", "review_status": "none"},
    ]
    service.approve_evaluation = MagicMock(return_value={})

    result = service.bulk_approve_evaluations("hack-1", "super-1")

    service.approve_evaluation.assert_called_once_with("ready", "super-1")
    assert result["approved_now"] == 1
    assert result["already_approved"] == 1
    assert result["skipped"] == 1
    assert result["failed"] == []
    assert result["leaderboard_eligible"] == 2


def test_bulk_approve_records_a_row_that_cannot_be_approved(service: SubmissionService):
    service.firebase.query_collection.return_value = [
        {"id": "ready", "status": "completed", "review_status": "pending_review"},
        {"id": "broken", "status": "completed", "review_status": "pending_review"},
    ]

    def approve(submission_id, _admin_id):
        if submission_id == "broken":
            raise ValueError("final_score is missing on this submission")
        return {}

    service.approve_evaluation = approve

    result = service.bulk_approve_evaluations("hack-1", "super-1")

    assert result["approved_now"] == 1
    assert result["failed"] == [
        {
            "submission_id": "broken",
            "reason": "final_score is missing on this submission",
        }
    ]
