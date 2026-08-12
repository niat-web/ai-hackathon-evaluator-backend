"""Phase 0: characterize accepted video MIME resolution (record + local upload)."""

import pytest

from app.utils.video_upload import (
    accepted_video_types_payload,
    resolve_video_content_type,
    resolve_video_content_type_from_metadata,
)


def test_accepted_video_types_includes_both_sources():
    payload = accepted_video_types_payload()
    assert "recorded" in payload["sources"]
    assert "uploaded" in payload["sources"]
    assert ".webm" in payload["allowed_extensions"]
    assert ".mp4" in payload["allowed_extensions"]
    assert payload["max_upload_bytes"] > 0
    assert payload["max_multipart_upload_bytes"] > 0
    assert payload["max_multipart_upload_bytes"] < payload["max_upload_bytes"]


def test_resolve_from_metadata_uses_filename_when_mime_missing():
    mime, ext = resolve_video_content_type_from_metadata(None, "demo.mp4")
    assert mime == "video/mp4"
    assert ext == ".mp4"


def test_resolve_from_metadata_handles_octet_stream_with_extension():
    mime, ext = resolve_video_content_type_from_metadata(
        "application/octet-stream", "clip.webm"
    )
    assert mime == "video/webm"
    assert ext == ".webm"


def test_resolve_webm_magic_bytes():
    # EBML header used by WebM
    header = b"\x1a\x45\xdf\xa3" + b"\x00" * 8
    mime, ext = resolve_video_content_type("application/octet-stream", "x.bin", header)
    assert mime == "video/webm"
    assert ext == ".webm"


def test_unsupported_type_raises():
    with pytest.raises(ValueError, match="Unsupported video format"):
        resolve_video_content_type_from_metadata("image/png", "photo.png")
