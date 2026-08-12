"""Phase 0: characterize submission access control (owner / assignee / admin)."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.user_model import CurrentUser
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
        return svc


def test_admin_can_read_any_submission(service: SubmissionService, admin_user: CurrentUser):
    doc = make_submission_doc()
    service.firebase.get_document.return_value = {k: v for k, v in doc.items() if k != "id"}
    result = service.get_submission("sub-1", admin_user)
    assert result is not None
    assert result["id"] == "sub-1"


def test_student_can_read_own_submission(service: SubmissionService, student_user: CurrentUser):
    doc = make_submission_doc(student_id=student_user.user_id)
    service.firebase.get_document.return_value = {k: v for k, v in doc.items() if k != "id"}
    result = service.get_submission("sub-1", student_user)
    assert result is not None


def test_student_cannot_read_others_submission(
    service: SubmissionService, student_user: CurrentUser
):
    doc = make_submission_doc(student_id="other-student")
    service.firebase.get_document.return_value = {k: v for k, v in doc.items() if k != "id"}
    assert service.get_submission("sub-1", student_user) is None


def test_evaluator_can_read_assigned_only(
    service: SubmissionService, evaluator_user: CurrentUser
):
    assigned = make_submission_doc(assigned_evaluator_id=evaluator_user.user_id)
    service.firebase.get_document.return_value = {
        k: v for k, v in assigned.items() if k != "id"
    }
    assert service.get_submission("sub-1", evaluator_user) is not None

    other = make_submission_doc(assigned_evaluator_id="someone-else")
    service.firebase.get_document.return_value = {k: v for k, v in other.items() if k != "id"}
    assert service.get_submission("sub-1", evaluator_user) is None


def test_assert_can_evaluate_allows_assignee_and_admin(
    service: SubmissionService, evaluator_user: CurrentUser, admin_user: CurrentUser
):
    doc = make_submission_doc(assigned_evaluator_id=evaluator_user.user_id)
    service.assert_can_evaluate(doc, evaluator_user)
    service.assert_can_evaluate(doc, admin_user)


def test_assert_can_evaluate_rejects_unassigned_evaluator(
    service: SubmissionService, evaluator_user: CurrentUser
):
    doc = make_submission_doc(assigned_evaluator_id="other")
    with pytest.raises(ValueError, match="assigned evaluator"):
        service.assert_can_evaluate(doc, evaluator_user)
