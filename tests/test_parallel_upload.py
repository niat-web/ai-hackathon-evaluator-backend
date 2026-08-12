"""Parallel compose + resumable prepare_direct_upload plans."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.user_model import CurrentUser
from app.services.submission.create import CreateMixin
from app.utils.gcs_video import compose_object_from_parts


class _Svc(CreateMixin):
    def __init__(self):
        self.bucket_name = "eval-bucket"
        self._client = MagicMock()

    def _validate_configuration(self, *, require_bucket: bool = True):
        return None

    def _storage_client(self):
        return self._client

    @staticmethod
    def _normalize_video_source(value):
        return value or "uploaded"


def _student() -> CurrentUser:
    return CurrentUser(
        user_id="stu-1",
        email="student@example.com",
        role="student",
        name="Student",
    )


def test_prepare_returns_parallel_parts_for_large_file(monkeypatch):
    monkeypatch.setenv("VIDEO_UPLOAD_PARALLEL_THRESHOLD_BYTES", str(32 * 1024 * 1024))
    monkeypatch.setenv("VIDEO_UPLOAD_PART_BYTES", str(8 * 1024 * 1024))
    monkeypatch.setenv("VIDEO_UPLOAD_PARALLEL_CONCURRENCY", "4")

    svc = _Svc()
    with patch(
        "app.services.submission.create.generate_signed_upload_url",
        side_effect=lambda *a, **k: f"https://signed.example/{a[2]}",
    ):
        plan = svc.prepare_direct_upload(
            student=_student(),
            filename="demo.mp4",
            content_type="video/mp4",
            video_source="uploaded",
            content_length=200 * 1024 * 1024,
            origin="http://localhost:5173",
        )

    assert plan["upload_protocol"] == "parallel_compose"
    assert plan["upload_url"] is None
    assert plan["recommended_concurrency"] == 4
    assert len(plan["parts"]) == 25  # 200 MiB / 8 MiB
    assert plan["parts"][0]["offset_start"] == 0
    assert plan["parts"][0]["offset_end"] == 8 * 1024 * 1024
    assert plan["parts"][-1]["offset_end"] == 200 * 1024 * 1024
    assert plan["required_headers"]["Content-Type"] == "video/mp4"
    assert plan["supports_progress"] is True


def test_prepare_uses_resumable_for_small_file():
    svc = _Svc()
    with patch(
        "app.services.submission.create.create_resumable_upload_url",
        return_value="https://storage.googleapis.com/upload/session",
    ) as resumable:
        plan = svc.prepare_direct_upload(
            student=_student(),
            filename="clip.webm",
            content_type="video/webm",
            content_length=5 * 1024 * 1024,
            origin="http://localhost:5173",
        )

    resumable.assert_called_once()
    assert plan["upload_protocol"] == "resumable"
    assert plan["upload_url"] == "https://storage.googleapis.com/upload/session"
    assert plan["parts"] == []


def test_compose_object_from_parts_deletes_sources():
    client = MagicMock()
    bucket = MagicMock()
    client.bucket.return_value = bucket

    part0 = MagicMock()
    part0.exists.return_value = True
    part0.name = "video.mp4.part000"
    part1 = MagicMock()
    part1.exists.return_value = True
    part1.name = "video.mp4.part001"
    dest = MagicMock()
    dest.size = 123

    def blob(name: str):
        if name.endswith(".part000"):
            return part0
        if name.endswith(".part001"):
            return part1
        return dest

    bucket.blob.side_effect = blob

    size = compose_object_from_parts(
        client,
        "bucket",
        "video.mp4",
        ["video.mp4.part000", "video.mp4.part001"],
        content_type="video/mp4",
    )
    assert size == 123
    dest.compose.assert_called_once()
    part0.delete.assert_called_once()
    part1.delete.assert_called_once()


def test_prepare_rejects_oversize_content_length():
    svc = _Svc()
    with pytest.raises(ValueError, match="too large"):
        svc.prepare_direct_upload(
            student=_student(),
            filename="huge.mp4",
            content_type="video/mp4",
            content_length=600 * 1024 * 1024,
        )


def test_prepare_auto_grows_part_size_to_stay_within_32_parts(monkeypatch):
    """500 MiB with 8 MiB parts would need 63 chunks — auto-size instead of error."""
    monkeypatch.setenv("VIDEO_UPLOAD_PARALLEL_THRESHOLD_BYTES", str(32 * 1024 * 1024))
    monkeypatch.setenv("VIDEO_UPLOAD_PART_BYTES", str(8 * 1024 * 1024))

    svc = _Svc()
    with patch(
        "app.services.submission.create.generate_signed_upload_url",
        side_effect=lambda *a, **k: f"https://signed.example/{a[2]}",
    ):
        plan = svc.prepare_direct_upload(
            student=_student(),
            filename="large.mp4",
            content_type="video/mp4",
            video_source="uploaded",
            content_length=500 * 1024 * 1024,
        )

    assert plan["upload_protocol"] == "parallel_compose"
    assert 1 <= len(plan["parts"]) <= 32
    assert plan["parts"][-1]["offset_end"] == 500 * 1024 * 1024
