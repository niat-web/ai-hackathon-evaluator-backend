"""Phase 3: race-safe evaluate / review / assign transitions via transactions."""

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
        svc.bucket_name = "test-bucket"
        svc.firebase.run_transaction.side_effect = lambda cb: cb(MagicMock())
        return svc


def _doc_without_id(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "id"}


def test_mark_queued_creates_analysis_and_links_submission(service: SubmissionService):
    base = make_submission_doc(status="uploaded", analysis_id=None)
    service.firebase.txn_get.return_value = _doc_without_id(base)

    analysis_id = service.mark_queued_for_evaluation(
        "sub-1",
        evaluation_criteria="Focus on UX",
        analyzed_by="evaluator-1",
    )

    assert isinstance(analysis_id, str) and len(analysis_id) == 32
    service.firebase.txn_set.assert_called_once()
    set_args = service.firebase.txn_set.call_args[0]
    assert set_args[1] == "analysis"
    assert set_args[2] == analysis_id
    assert set_args[3]["status"] == "processing"
    assert set_args[3]["evaluation_criteria"] == "Focus on UX"

    update_args = service.firebase.txn_update.call_args[0]
    assert update_args[1] == "submissions"
    assert update_args[2] == "sub-1"
    assert update_args[3]["status"] == "processing"
    assert update_args[3]["analysis_id"] == analysis_id
    assert update_args[3]["review_status"] == "none"
    assert update_args[3]["report_published"] is False


def test_mark_queued_rejects_already_processing(service: SubmissionService):
    base = make_submission_doc(status="processing")
    service.firebase.txn_get.return_value = _doc_without_id(base)

    with pytest.raises(ValueError, match="already being analyzed"):
        service.mark_queued_for_evaluation("sub-1")

    service.firebase.txn_set.assert_not_called()
    service.firebase.txn_update.assert_not_called()


def test_mark_queued_rejects_missing_submission(service: SubmissionService):
    service.firebase.txn_get.return_value = None
    with pytest.raises(ValueError, match="not found"):
        service.mark_queued_for_evaluation("missing")


def test_submit_for_review_rejects_already_pending(service: SubmissionService):
    base = make_submission_doc(status="completed", review_status="pending_review")

    def txn_get(_txn, collection, _doc_id):
        if collection == "analysis":
            return {"status": "completed"}
        return _doc_without_id(base)

    service.firebase.txn_get.side_effect = txn_get

    with pytest.raises(ValueError, match="already pending"):
        service.submit_for_review("sub-1", "evaluator-1", 90)


def test_assign_evaluator_clears_pending_review(service: SubmissionService):
    base = make_submission_doc(
        status="completed",
        review_status="pending_review",
        assigned_evaluator_id="evaluator-1",
    )
    service.firebase.txn_get.return_value = _doc_without_id(base)
    service._require_active_evaluator = MagicMock(
        return_value={"id": "evaluator-2", "name": "Eva Two"}
    )

    result = service.assign_evaluator("sub-1", "evaluator-2", assigned_by="admin-1")

    payload = service.firebase.txn_update.call_args[0][3]
    assert payload["assigned_evaluator_id"] == "evaluator-2"
    assert payload["review_status"] == "none"
    assert payload["submitted_for_review_at"] is None
    assert result["assigned_evaluator_id"] == "evaluator-2"


def test_commit_analysis_and_submission_uses_batch(service: SubmissionService):
    service._commit_analysis_and_submission(
        analysis_id="a-1",
        submission_id="sub-1",
        analysis_data={"status": "completed", "report": "# r", "error": None},
        submission_data={"status": "completed", "error": None},
    )

    service.firebase.batch_write.assert_called_once()
    ops = service.firebase.batch_write.call_args[0][0]
    assert len(ops) == 2
    assert ops[0]["collection"] == "analysis"
    assert ops[0]["document_id"] == "a-1"
    assert ops[0]["data"]["status"] == "completed"
    assert ops[1]["collection"] == "submissions"
    assert ops[1]["data"]["status"] == "completed"


def test_divide_equally_uses_batch_write(service: SubmissionService):
    service.hackathon_service.get_hackathon.return_value = {"id": "h-1", "name": "H"}
    docs = {
        "s1": {"hackathon_id": "h-1", "assigned_evaluator_id": None},
        "s2": {"hackathon_id": "h-1", "assigned_evaluator_id": None},
    }
    service.firebase.get_document.side_effect = lambda c, i: docs[i]
    service._resolve_active_evaluators = MagicMock(
        return_value=[
            {"id": "e1", "name": "E1"},
            {"id": "e2", "name": "E2"},
        ]
    )

    with patch("random.shuffle", side_effect=lambda x: None):
        result = service.divide_equally_among_evaluators(
            hackathon_id="h-1",
            submission_ids=["s1", "s2"],
            assigned_by="admin-1",
        )

    service.firebase.batch_write.assert_called_once()
    ops = service.firebase.batch_write.call_args[0][0]
    assert len(ops) == 2
    assert {op["document_id"] for op in ops} == {"s1", "s2"}
    assert len(result) == 2
    assert all(item["assigned_evaluator_id"] in ("e1", "e2") for item in result)
