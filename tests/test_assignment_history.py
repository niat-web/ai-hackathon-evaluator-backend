"""Evaluator reassignment requires a reason and appends assignment_history."""

from unittest.mock import MagicMock, patch

import pytest

from app.exceptions import BadRequestError
from app.services.submission_service import SubmissionService
from tests.conftest import make_submission_doc


@pytest.fixture
def service() -> SubmissionService:
    with patch.object(SubmissionService, "__init__", lambda self: None):
        svc = SubmissionService()
        svc.collection = "submissions"
        svc.firebase = MagicMock()
        svc.user_service = MagicMock()
        svc.hackathon_service = MagicMock()
        svc.firebase.run_transaction.side_effect = lambda callback: callback(MagicMock())
        return svc


def _doc_without_id(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "id"}


def test_first_assignment_needs_no_reason_and_is_logged(service: SubmissionService):
    base = make_submission_doc(assigned_evaluator_id=None, status="uploaded")
    service.firebase.txn_get.return_value = _doc_without_id(base)
    service._require_active_evaluator = MagicMock(return_value={"id": "e2", "name": "Bea"})
    service._assert_evaluator_on_hackathon = MagicMock()

    result = service.assign_evaluator(
        "sub-1",
        "e2",
        assigned_by="sa",
        assigned_by_name="Debashis Nayak",
    )

    entry = result["assignment_history"][-1]
    assert entry["from_evaluator_id"] is None
    assert entry["to_evaluator_id"] == "e2"
    assert entry["to_evaluator_name"] == "Bea"
    assert entry["reason"] is None
    assert entry["evaluation_submitted"] is False
    assert entry["changed_by_name"] == "Debashis Nayak"
    assert entry["changed_at"].endswith("+05:30")


def test_reassign_without_a_reason_is_rejected(service: SubmissionService):
    base = make_submission_doc(assigned_evaluator_id="e1")
    service.firebase.txn_get.return_value = _doc_without_id(base)
    service._require_active_evaluator = MagicMock(return_value={"id": "e2", "name": "Bea"})
    service._assert_evaluator_on_hackathon = MagicMock()

    with pytest.raises(BadRequestError) as caught:
        service.assign_evaluator("sub-1", "e2", assigned_by="sa", reason="  ")

    assert caught.value.code == "REASON_REQUIRED"
    assert str(caught.value) == "Give a reason for changing the evaluator."
    service.firebase.txn_update.assert_not_called()


def test_reassign_after_a_score_keeps_history_and_the_score(service: SubmissionService):
    base = make_submission_doc(
        assigned_evaluator_id="e1",
        review_status="none",
        final_score=88,
    )
    base["assignment_history"] = [
        {
            "changed_at": "2026-10-01T10:00:00+05:30",
            "changed_by_id": "sa",
            "changed_by_name": "Debashis Nayak",
            "from_evaluator_id": None,
            "from_evaluator_name": None,
            "to_evaluator_id": "e1",
            "to_evaluator_name": "Evaluator One",
            "reason": None,
            "evaluation_submitted": False,
        }
    ]
    service.firebase.txn_get.return_value = _doc_without_id(base)
    service._require_active_evaluator = MagicMock(
        return_value={"id": "e2", "name": "Dubbala Narayana"}
    )
    service._assert_evaluator_on_hackathon = MagicMock()

    result = service.assign_evaluator(
        "sub-1",
        "e2",
        assigned_by="sa",
        assigned_by_name="Debashis Nayak",
        reason="Conflict of interest with this team",
    )

    history = result["assignment_history"]
    assert len(history) == 2
    assert history[0]["to_evaluator_id"] == "e1"
    latest = history[1]
    assert latest["from_evaluator_id"] == "e1"
    assert latest["from_evaluator_name"] == "Evaluator One"
    assert latest["to_evaluator_id"] == "e2"
    assert latest["to_evaluator_name"] == "Dubbala Narayana"
    assert latest["reason"] == "Conflict of interest with this team"
    assert latest["evaluation_submitted"] is True
    assert result["final_score"] == 88
    assert result["review_status"] == "none"
    assert "final_score" not in service.firebase.txn_update.call_args[0][3]


def test_unassign_does_not_require_a_reason(service: SubmissionService):
    base = make_submission_doc(
        assigned_evaluator_id="e1",
        review_status="changes_requested",
    )
    service.firebase.txn_get.return_value = _doc_without_id(base)

    result = service.assign_evaluator(
        "sub-1",
        None,
        assigned_by="sa",
        assigned_by_name="Debashis Nayak",
    )

    entry = result["assignment_history"][-1]
    assert result["assigned_evaluator_id"] is None
    assert result["review_status"] == "none"
    assert entry["from_evaluator_id"] == "e1"
    assert entry["to_evaluator_id"] is None
    assert entry["reason"] is None
    assert entry["evaluation_submitted"] is True


def test_picking_the_same_evaluator_does_not_append_history(service: SubmissionService):
    base = make_submission_doc(assigned_evaluator_id="e1", review_status="pending_review")
    service.firebase.txn_get.return_value = _doc_without_id(base)
    service._require_active_evaluator = MagicMock(
        return_value={"id": "e1", "name": "Evaluator One"}
    )
    service._assert_evaluator_on_hackathon = MagicMock()

    result = service.assign_evaluator("sub-1", "e1", assigned_by="sa")

    assert result["review_status"] == "pending_review"
    assert "assignment_history" not in result
    service.firebase.txn_update.assert_not_called()
