"""Phase 4: upload size limits and spooling (multipart + signed finalize)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.submission_service import SubmissionService
from app.utils.video_upload import (
    MAX_MULTIPART_VIDEO_BYTES,
    MAX_VIDEO_UPLOAD_BYTES,
    accepted_video_types_payload,
    assert_multipart_request_content_length,
    assert_video_size,
    peek_file_header,
    spool_upload_file,
)


def test_accepted_types_exposes_multipart_and_signed_limits():
    payload = accepted_video_types_payload()
    assert payload["max_upload_bytes"] == MAX_VIDEO_UPLOAD_BYTES
    assert payload["max_multipart_upload_bytes"] == MAX_MULTIPART_VIDEO_BYTES
    assert payload["max_multipart_upload_bytes"] < payload["max_upload_bytes"]


def test_assert_video_size_allows_within_limit():
    assert_video_size(1024, max_bytes=MAX_MULTIPART_VIDEO_BYTES, via="multipart")


def test_assert_video_size_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        assert_video_size(0, max_bytes=MAX_MULTIPART_VIDEO_BYTES, via="multipart")


def test_assert_video_size_rejects_multipart_oversize():
    with pytest.raises(ValueError, match="too large for multipart"):
        assert_video_size(
            MAX_MULTIPART_VIDEO_BYTES + 1,
            max_bytes=MAX_MULTIPART_VIDEO_BYTES,
            via="multipart",
        )


def test_assert_video_size_rejects_signed_oversize():
    with pytest.raises(ValueError, match="too large"):
        assert_video_size(
            MAX_VIDEO_UPLOAD_BYTES + 1,
            max_bytes=MAX_VIDEO_UPLOAD_BYTES,
            via="signed",
        )


def test_assert_multipart_content_length_rejects_over_cloud_run():
    with pytest.raises(ValueError, match="too large"):
        assert_multipart_request_content_length(str(40 * 1024 * 1024))


def test_assert_multipart_content_length_allows_small_or_missing():
    assert_multipart_request_content_length(None)
    assert_multipart_request_content_length(str(1024))


@pytest.mark.asyncio
async def test_spool_upload_file_accepts_small_webm():
    header = b"\x1a\x45\xdf\xa3" + b"\x00" * 100
    upload = MagicMock()
    upload.read = AsyncMock(side_effect=[header, b""])

    spool = await spool_upload_file(upload, max_bytes=MAX_MULTIPART_VIDEO_BYTES)
    try:
        assert peek_file_header(spool)[:4] == b"\x1a\x45\xdf\xa3"
        spool.seek(0, 2)
        assert spool.tell() == len(header)
    finally:
        spool.close()


@pytest.mark.asyncio
async def test_spool_upload_file_rejects_oversize():
    chunk = b"x" * (1024 * 1024)
    # Stream more than the multipart limit.
    chunks = [chunk] * ((MAX_MULTIPART_VIDEO_BYTES // len(chunk)) + 2) + [b""]
    upload = MagicMock()
    upload.read = AsyncMock(side_effect=chunks)

    with pytest.raises(ValueError, match="too large for multipart"):
        await spool_upload_file(upload, max_bytes=MAX_MULTIPART_VIDEO_BYTES)


def test_http_from_value_error_maps_too_large_to_413():
    from app.routes.submissions import _http_from_value_error

    exc = _http_from_value_error(ValueError("Video is too large for multipart upload"))
    assert exc.status_code == 413


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
        svc.storage_client = MagicMock()
        return svc


def test_create_submission_from_upload_rejects_oversize_blob(service: SubmissionService):
    service.hackathon_service.get_hackathon.return_value = {
        "id": "hack-1",
        "name": "H",
        "theme_ids": ["theme-1"],
    }
    service.theme_service.get_theme.return_value = {"id": "theme-1", "name": "T"}
    service._resolve_student_team_name = MagicMock(return_value="team peek")
    service._validate_configuration = MagicMock()

    blob = MagicMock()
    blob.exists.return_value = True
    blob.size = MAX_VIDEO_UPLOAD_BYTES + 10
    service._storage_client = MagicMock(
        return_value=MagicMock(
            bucket=MagicMock(return_value=MagicMock(blob=MagicMock(return_value=blob)))
        )
    )

    student = MagicMock(user_id="student-1")
    with pytest.raises(ValueError, match="too large"):
        service.create_submission_from_upload(
            student=student,
            video_path="gs://test-bucket/submissions/student-1/abc123/video.webm",
            content_type="video/webm",
            source_filename="demo.webm",
            problem_statement="Problem",
            solution_description="Solution",
            hackathon_id="hack-1",
            theme_id="theme-1",
            video_source="uploaded",
        )


def test_create_submission_rejects_oversize_bytes(service: SubmissionService):
    service.hackathon_service.get_hackathon.return_value = {
        "id": "hack-1",
        "name": "H",
        "theme_ids": ["theme-1"],
    }
    service.theme_service.get_theme.return_value = {"id": "theme-1", "name": "T"}
    service._resolve_student_team_name = MagicMock(return_value="team peek")
    service._validate_configuration = MagicMock()

    student = MagicMock(user_id="student-1")
    huge = b"\x1a\x45\xdf\xa3" + b"\x00" * (MAX_MULTIPART_VIDEO_BYTES)
    with pytest.raises(ValueError, match="too large for multipart"):
        service.create_submission(
            student=student,
            video=("demo.webm", huge, "video/webm"),
            problem_statement="Problem",
            solution_description="Solution",
            hackathon_id="hack-1",
            theme_id="theme-1",
        )
