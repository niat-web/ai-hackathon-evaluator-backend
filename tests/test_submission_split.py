"""Phase 10: submission package split + shared create builders."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.user_model import CurrentUser
from app.services.submission.create import CREATE_SUCCESS_MESSAGE, CreateMixin
from app.services.submission_service import SubmissionService


def _stub_create_mixin() -> CreateMixin:
    """Minimal CreateMixin host with mocked collaborators."""

    class Host(CreateMixin):
        collection = "submissions"

        def __init__(self):
            self.bucket_name = "eval-bucket"
            self.hackathon_service = MagicMock()
            self.theme_service = MagicMock()
            self.firebase = MagicMock()
            self._team = "Team Alpha"

        def _validate_configuration(self, *, require_bucket: bool = True):
            return None

        def _resolve_student_team_name(self, student_id: str) -> str:
            return self._team

        def _storage_client(self):
            return MagicMock()

    return Host()


def test_shared_validate_hackathon_and_theme_rejects_unreleased_theme():
    host = _stub_create_mixin()
    host.hackathon_service.get_hackathon.return_value = {
        "name": "H1",
        "theme_ids": ["theme-a"],
    }
    with pytest.raises(ValueError, match="not released"):
        host._validate_hackathon_and_theme("hack-1", "theme-b")


def test_build_new_submission_document_shape_is_stable():
    host = _stub_create_mixin()
    hackathon = {"name": "Hack"}
    theme = {"name": "AI"}
    doc = host._build_new_submission_document(
        student_id="stu-1",
        hackathon_id="hack-1",
        hackathon=hackathon,
        theme_id="theme-1",
        theme=theme,
        team_name="Team Alpha",
        problem_statement=" problem ",
        solution_description=" solution ",
        video_path="gs://eval-bucket/submissions/stu-1/id/video.webm",
        content_type="video/webm",
        source_filename="demo.webm",
        video_source="recorded",
        now="2026-01-01T00:00:00",
    )
    assert doc["status"] == "uploaded"
    assert doc["review_status"] == "none"
    assert doc["problem_statement"] == "problem"
    assert doc["solution_description"] == "solution"
    assert doc["video_source"] == "recorded"
    assert doc["hackathon_name"] == "Hack"
    assert doc["theme_name"] == "AI"
    assert doc["created_at"] == doc["updated_at"] == "2026-01-01T00:00:00"


def test_normalize_video_source():
    assert CreateMixin._normalize_video_source("recorded") == "recorded"
    assert CreateMixin._normalize_video_source("uploaded") == "uploaded"
    assert CreateMixin._normalize_video_source("other") is None
    assert CreateMixin._normalize_video_source(None) is None


def test_persist_new_submission_uses_shared_message():
    host = _stub_create_mixin()
    submission = {"status": "uploaded"}
    result = host._persist_new_submission("sub-1", submission)
    assert result["id"] == "sub-1"
    assert result["message"] == CREATE_SUCCESS_MESSAGE
    host.firebase.set_document.assert_called_once_with(
        "submissions", "sub-1", submission
    )


def test_facade_still_exports_from_submission_service_module():
    assert SubmissionService.__module__ == "app.services.submission.service"


def test_create_submission_uses_shared_builders():
    host = _stub_create_mixin()
    host.hackathon_service.get_hackathon.return_value = {
        "name": "H1",
        "theme_ids": ["t1"],
    }
    host.theme_service.get_theme.return_value = {"name": "Theme"}
    host._upload_bytes = MagicMock()
    host._upload_fileobj = MagicMock()

    student = CurrentUser(
        user_id="stu-1",
        email="s@x.com",
        role="student",
        name="S",
        approval_status="approved",
    )
    result = host.create_submission(
        student=student,
        video=("demo.webm", b"\x00" * 64, "video/webm"),
        problem_statement="p",
        solution_description="s",
        hackathon_id="hack-1",
        theme_id="t1",
        video_source="recorded",
    )
    assert result["message"] == CREATE_SUCCESS_MESSAGE
    assert result["hackathon_name"] == "H1"
    assert result["theme_name"] == "Theme"
    assert result["team_name"] == "Team Alpha"
    assert result["status"] == "uploaded"
    host.firebase.set_document.assert_called_once()
    host._upload_bytes.assert_called_once()
