"""Admin withdraw deletes the submission so the student can submit again."""

from unittest.mock import MagicMock, patch

import pytest

from app.exceptions import ConflictError, NotFoundError
from app.services.submission_service import SubmissionService


def _service() -> SubmissionService:
    with patch.object(SubmissionService, "__init__", lambda self: None):
        service = SubmissionService()
    service.collection = "submissions"
    service.analysis_collection = "analysis"
    service.bucket_name = "eval-bucket"
    service.firebase = MagicMock()
    service.storage_client = MagicMock()
    return service


def test_withdraw_deletes_submission_analysis_and_video():
    service = _service()
    service.firebase.get_document.return_value = {
        "hackathon_id": "h1",
        "student_id": "stu-1",
        "analysis_id": "analysis-1",
        "video_path": "gs://eval-bucket/submissions/stu-1/sub-1/video.mp4",
        "problem_statement": "wrong",
    }

    result = service.withdraw_submission("sub-1", withdrawn_by="admin-1")

    assert result == {"id": "sub-1", "withdrawn": True}
    blob = service.storage_client.bucket.return_value.blob
    blob.assert_called_once_with("submissions/stu-1/sub-1/video.mp4")
    blob.return_value.delete.assert_called_once()
    service.firebase.delete_document.assert_any_call("analysis", "analysis-1")
    service.firebase.delete_document.assert_any_call("submissions", "sub-1")


def test_withdraw_allows_completed_ai_analysis_before_assignment():
    service = _service()
    service.firebase.get_document.return_value = {
        "hackathon_id": "h1",
        "student_id": "stu-1",
        "status": "completed",
        "analysis_id": "analysis-1",
        "assigned_evaluator_id": None,
        "video_path": None,
    }

    result = service.withdraw_submission("sub-3", withdrawn_by="admin-1")

    assert result["withdrawn"] is True
    service.firebase.delete_document.assert_any_call("submissions", "sub-3")


def test_withdraw_rejects_submission_already_assigned():
    service = _service()
    service.firebase.get_document.return_value = {
        "hackathon_id": "h1",
        "student_id": "stu-1",
        "status": "completed",
        "analysis_id": "analysis-1",
        "assigned_evaluator_id": "eval-1",
        "video_path": "gs://eval-bucket/submissions/stu-1/sub-4/video.mp4",
    }

    with pytest.raises(ConflictError, match="assigned") as raised:
        service.withdraw_submission("sub-4", withdrawn_by="admin-1")

    assert raised.value.code == "SUBMISSION_ASSIGNED"
    service.firebase.delete_document.assert_not_called()
    service.storage_client.bucket.assert_not_called()


def test_withdraw_missing_submission_is_not_found():
    service = _service()
    service.firebase.get_document.return_value = None

    with pytest.raises(NotFoundError):
        service.withdraw_submission("missing", withdrawn_by="admin-1")

    service.firebase.delete_document.assert_not_called()


def test_withdraw_without_video_still_removes_the_document():
    service = _service()
    service.firebase.get_document.return_value = {
        "hackathon_id": "h1",
        "student_id": "stu-1",
        "analysis_id": None,
        "video_path": None,
    }

    service.withdraw_submission("sub-2", withdrawn_by="admin-1")

    service.storage_client.bucket.assert_not_called()
    service.firebase.delete_document.assert_called_once_with("submissions", "sub-2")


def test_video_delete_failure_still_removes_firestore_rows():
    service = _service()
    service.firebase.get_document.return_value = {
        "hackathon_id": "h1",
        "student_id": "stu-1",
        "analysis_id": "analysis-9",
        "video_path": "gs://eval-bucket/submissions/stu-1/sub-9/video.webm",
    }
    service.storage_client.bucket.return_value.blob.return_value.delete.side_effect = RuntimeError(
        "gone"
    )

    result = service.withdraw_submission("sub-9", withdrawn_by="admin-1")

    assert result["withdrawn"] is True
    service.firebase.delete_document.assert_any_call("analysis", "analysis-9")
    service.firebase.delete_document.assert_any_call("submissions", "sub-9")
