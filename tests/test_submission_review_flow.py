"""Phase 0/3: characterize review workflow state transitions (transactional)."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.submission_service import SubmissionService
from tests.conftest import make_submission_doc


@pytest.fixture
def service() -> SubmissionService:
    with patch.object(SubmissionService, "__init__", lambda self: None):
        svc = SubmissionService()
        svc.collection = "submissions"
        svc.analysis_collection = "analysis"
        svc.firebase = MagicMock()
        svc.user_service = MagicMock()
        svc.hackathon_service = MagicMock()
        svc.theme_service = MagicMock()
        svc.metric_scoring_service = MagicMock()
        svc.metric_scoring_service.get_scoring_for_requirement.return_value = None
        svc.bucket_name = "test-bucket"
        # Execute transactional callbacks immediately (no real Firestore).
        svc.firebase.run_transaction.side_effect = lambda cb: cb(MagicMock())
        return svc


def _doc_without_id(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "id"}


def _wire_reads(
    service: SubmissionService,
    submission: dict,
    analysis: dict | None = None,
) -> None:
    def txn_get(_txn, collection: str, _doc_id: str):
        if collection == service.analysis_collection:
            return analysis
        return submission

    service.firebase.txn_get.side_effect = txn_get


def test_submit_for_review_sets_pending_and_score(service: SubmissionService):
    base = make_submission_doc(status="completed", review_status="none")
    analysis = {"status": "completed", "report": "# ok", "checklist": "c"}
    _wire_reads(service, _doc_without_id(base), analysis)

    result = service.submit_for_review(
        submission_id="sub-1",
        evaluator_user_id="evaluator-1",
        final_score=82,
        evaluator_notes="Looks good",
    )

    service.firebase.txn_update.assert_called_once()
    payload = service.firebase.txn_update.call_args[0][3]
    assert payload["review_status"] == "pending_review"
    assert payload["final_score"] == 82.0
    assert payload["report_published"] is False
    assert payload["submitted_for_review_by"] == "evaluator-1"
    assert result["review_status"] == "pending_review"


def test_submit_for_review_applies_ai_overrides(service: SubmissionService):
    from app.services.scorecard import apply_ai_field_scores, build_scorecard_skeleton
    from tests.test_scorecard import SAMPLE_METRICS

    base = make_submission_doc(status="completed", review_status="none")
    card = apply_ai_field_scores(
        build_scorecard_skeleton(SAMPLE_METRICS),
        [
            {"field_key": "problem_statement", "score": 15, "max_score": 15},
            {"field_key": "solution_description", "score": 15, "max_score": 15},
            {"field_key": "video_explanation", "score": 20, "max_score": 20},
        ],
    )
    analysis = {
        "status": "completed",
        "report": "# ok",
        "scorecard": card,
        "field_scores": [],
    }
    _wire_reads(service, _doc_without_id(base), analysis)
    service.hackathon_service.get_hackathon.return_value = {
        "id": "hack-1",
        "timeline": [{"evaluation_requirement_id": "req-1"}],
    }
    service.metric_scoring_service.get_scoring_for_requirement.return_value = {
        "metrics": SAMPLE_METRICS,
    }

    result = service.submit_for_review(
        submission_id="sub-1",
        evaluator_user_id="evaluator-1",
        override_ai_scores=True,
        ai_overrides=[
            {"field_key": "problem_statement", "score": 0},
            {"field_key": "solution_description", "score": 1},
            {"field_key": "video_explanation", "score": 16.5},
        ],
        manual_metrics=[
            {
                "field_key": "github_link",
                "segments": [
                    {"key": "visibility", "value": "public"},
                    {"key": "structure_score", "score": 0},
                ],
            },
            {
                "field_key": "mvp_link",
                "segments": [
                    {"key": "authentication", "value": False},
                    {"key": "data_persistence", "value": False},
                    {"key": "realtime_data", "value": False},
                    {"key": "ai_features", "value": False},
                    {"key": "mobile_responsive", "value": False},
                    {"key": "ui_quality", "value": False},
                ],
            },
        ],
    )

    # First txn_update is submission; scorecard should reflect overrides.
    sub_update = service.firebase.txn_update.call_args_list[0][0][3]
    assert sub_update["override_ai_scores"] is True
    assert sub_update["final_score"] == 17.5
    assert sub_update["scorecard"]["ai_total"] == 17.5
    ps = next(
        m
        for m in sub_update["scorecard"]["metrics"]
        if m["field_key"] == "problem_statement"
    )
    assert ps["source"] == "evaluator_override"
    assert ps["score"] == 0
    assert result["evaluator_ai_overrides"][0]["original_ai_score"] == 15


def test_submit_for_review_rejects_non_assignee(service: SubmissionService):
    base = make_submission_doc(assigned_evaluator_id="evaluator-1")
    _wire_reads(service, _doc_without_id(base), {"status": "completed"})
    with pytest.raises(ValueError, match="assigned evaluator"):
        service.submit_for_review("sub-1", "other-eval", 50)


def test_submit_for_review_requires_completed_analysis(service: SubmissionService):
    base = make_submission_doc(status="uploaded")
    _wire_reads(service, _doc_without_id(base), {"status": "completed"})
    with pytest.raises(ValueError, match="completed"):
        service.submit_for_review("sub-1", "evaluator-1", 50)


def test_approve_evaluation_publishes_and_sets_approved(service: SubmissionService):
    base = make_submission_doc(
        status="completed",
        review_status="pending_review",
        final_score=80,
    )
    _wire_reads(service, _doc_without_id(base), {"status": "completed"})

    result = service.approve_evaluation(
        submission_id="sub-1",
        admin_user_id="admin-1",
        final_score=85,
        review_notes="Approved",
    )

    payload = service.firebase.txn_update.call_args[0][3]
    assert payload["review_status"] == "approved"
    assert payload["report_published"] is True
    assert payload["final_score"] == 85.0
    assert payload["published_by"] == "admin-1"
    assert result["report_published"] is True


def test_approve_requires_pending_or_approved(service: SubmissionService):
    base = make_submission_doc(status="completed", review_status="none")
    _wire_reads(service, _doc_without_id(base), {"status": "completed"})
    with pytest.raises(ValueError, match="submitted for review"):
        service.approve_evaluation("sub-1", "admin-1", final_score=70)


def test_request_changes_unpublishes(service: SubmissionService):
    base = make_submission_doc(
        review_status="pending_review",
        report_published=False,
        final_score=70,
    )
    _wire_reads(service, _doc_without_id(base))

    service.request_evaluation_changes("sub-1", "admin-1", review_notes="Fix scoring")
    payload = service.firebase.txn_update.call_args[0][3]
    assert payload["review_status"] == "changes_requested"
    assert payload["report_published"] is False
