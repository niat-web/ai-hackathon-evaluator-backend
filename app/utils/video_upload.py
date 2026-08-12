"""
Video upload type detection and size limits for browser recordings and file uploads.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from fastapi import UploadFile


# MIME types accepted for hackathon submission videos (record OR local upload).
ALLOWED_VIDEO_TYPES: dict[str, str] = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/mpeg": ".mpeg",
    "video/ogg": ".ogv",
    "video/x-msvideo": ".avi",
}

# Browser MediaRecorder / OS file pickers may send these instead of base MIME types.
MIME_ALIASES: dict[str, str] = {
    "video/x-webm": "video/webm",
    "application/webm": "video/webm",
    "video/avi": "video/x-msvideo",
    # Some OS pickers omit a real type; resolve via filename extension instead.
    "application/octet-stream": "",
    "binary/octet-stream": "",
}

# Signed PUT → GCS path (no Cloud Run body cap). Enforced server-side on finalize.
MAX_VIDEO_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MiB

# Cloud Run HTTP/1 rejects request bodies around 32 MiB with 413.
CLOUD_RUN_REQUEST_BODY_LIMIT_BYTES = 32 * 1024 * 1024

# Multipart video payload soft cap (leaves headroom for form fields + boundaries).
MAX_MULTIPART_VIDEO_BYTES = 28 * 1024 * 1024  # 28 MiB

# Chunk size when spooling multipart video to disk/memory.
_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024  # 1 MiB

# Keep small recordings in RAM; spill larger ones to a temp file.
_SPOOL_MAX_MEMORY_BYTES = 1 * 1024 * 1024  # 1 MiB

# HTML accept= attribute helper for <input type="file">.
FILE_INPUT_ACCEPT = ",".join(
    sorted(
        {
            *ALLOWED_VIDEO_TYPES.keys(),
            *ALLOWED_VIDEO_TYPES.values(),
        }
    )
)

EXTENSION_TO_MIME: dict[str, str] = {
    ext: mime for mime, ext in ALLOWED_VIDEO_TYPES.items()
}


def _normalize_mime(content_type: str | None) -> str | None:
    if not content_type:
        return None

    base = content_type.split(";")[0].strip().lower()
    if base in ALLOWED_VIDEO_TYPES:
        return base

    aliased = MIME_ALIASES.get(base)
    # Empty alias means "ignore MIME; fall back to filename/magic".
    if aliased == "":
        return None
    return aliased


def accepted_video_types_payload() -> dict:
    """Public metadata for frontend Record / Upload pickers."""
    return {
        "allowed_mime_types": sorted(ALLOWED_VIDEO_TYPES.keys()),
        "allowed_extensions": sorted(ALLOWED_VIDEO_TYPES.values()),
        "file_input_accept": FILE_INPUT_ACCEPT,
        "max_upload_bytes": MAX_VIDEO_UPLOAD_BYTES,
        "max_multipart_upload_bytes": MAX_MULTIPART_VIDEO_BYTES,
        "sources": ["recorded", "uploaded"],
        "note": (
            "Use POST /submissions/upload-url with content_length=File.size, "
            "PUT directly to GCS (parallel parts for large files), then "
            "POST /submissions/from-upload. Never multipart large videos "
            f"(Cloud Run limit ~{MAX_MULTIPART_VIDEO_BYTES // (1024 * 1024)} MiB)."
        ),
        "prefer_direct_gcs": True,
        "send_content_length_for_parallel": True,
    }


def assert_video_size(
    size_bytes: int,
    *,
    max_bytes: int,
    via: str = "upload",
) -> None:
    """
    Reject oversized videos with a clear message (maps to HTTP 413 upstream).

    ``via`` is ``\"multipart\"`` or ``\"signed\"`` (or a free-form label).
    """
    if size_bytes < 0:
        raise ValueError("Invalid video size")
    if size_bytes == 0:
        raise ValueError("Uploaded video is empty")
    if size_bytes <= max_bytes:
        return

    max_mib = max_bytes / (1024 * 1024)
    got_mib = size_bytes / (1024 * 1024)
    if via == "multipart":
        raise ValueError(
            f"Video is too large for multipart upload "
            f"({got_mib:.1f} MiB; max {max_mib:.0f} MiB). "
            "Use POST /submissions/upload-url and POST /submissions/from-upload "
            "for larger demos."
        )
    raise ValueError(
        f"Video is too large ({got_mib:.1f} MiB; max {max_mib:.0f} MiB)."
    )


def assert_multipart_request_content_length(content_length: str | None) -> None:
    """
    Fail fast when Content-Length already exceeds the Cloud Run body limit.

    Missing / unparsable Content-Length is ignored (chunked bodies); the spool
    reader still enforces ``MAX_MULTIPART_VIDEO_BYTES``.
    """
    if not content_length:
        return
    try:
        length = int(content_length)
    except ValueError:
        return
    if length > CLOUD_RUN_REQUEST_BODY_LIMIT_BYTES:
        raise ValueError(
            "Request body is too large for multipart upload "
            f"(Content-Length {length} bytes; Cloud Run limit is about "
            f"{CLOUD_RUN_REQUEST_BODY_LIMIT_BYTES // (1024 * 1024)} MiB). "
            "Use POST /submissions/upload-url and POST /submissions/from-upload."
        )


async def spool_upload_file(
    upload: UploadFile,
    *,
    max_bytes: int = MAX_MULTIPART_VIDEO_BYTES,
) -> SpooledTemporaryFile:
    """
    Read an UploadFile in chunks into a spooled temp file, enforcing ``max_bytes``.

    Small files stay in memory; larger ones spill to disk so the process does not
    hold the full video as a single ``bytes`` object longer than needed.
    """
    spool: SpooledTemporaryFile = SpooledTemporaryFile(max_size=_SPOOL_MAX_MEMORY_BYTES)
    total = 0
    try:
        while True:
            chunk = await upload.read(_UPLOAD_READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                spool.close()
                assert_video_size(total, max_bytes=max_bytes, via="multipart")
            spool.write(chunk)
    except Exception:
        spool.close()
        raise

    assert_video_size(total, max_bytes=max_bytes, via="multipart")
    spool.seek(0)
    return spool


def peek_file_header(fileobj: BinaryIO, nbytes: int = 64) -> bytes:
    """Read a small header for magic-byte detection, then rewind."""
    pos = fileobj.tell()
    header = fileobj.read(nbytes)
    fileobj.seek(pos)
    return header or b""


def _mime_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None

    extension = Path(filename).suffix.lower()
    return EXTENSION_TO_MIME.get(extension)


def _mime_from_magic(file_bytes: bytes) -> str | None:
    if len(file_bytes) < 12:
        return None

    header = file_bytes[:12]

    # WebM / Matroska (common for browser screen recordings)
    if header[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"

    # MP4 / MOV (ftyp at offset 4)
    if header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand.startswith(b"qt"):
            return "video/quicktime"
        return "video/mp4"

    # MPEG program stream
    if header[:3] == b"\x00\x00\x01":
        return "video/mpeg"

    return None


def resolve_video_content_type(
    content_type: str | None,
    filename: str | None,
    file_bytes: bytes,
) -> tuple[str, str]:
    """
    Resolve a supported video MIME type and file extension.

    Browser screen recordings often send values like
    ``video/webm;codecs=vp9,opus`` or ``application/octet-stream`` which are
    normalized here using the base MIME type, filename extension, or magic bytes.

    Returns:
        Tuple of (content_type, extension)

    Raises:
        ValueError: If the upload cannot be recognized as a supported video.
    """
    candidates = [
        _normalize_mime(content_type),
        _mime_from_filename(filename),
        _mime_from_magic(file_bytes),
    ]

    for mime in candidates:
        if mime and mime in ALLOWED_VIDEO_TYPES:
            return mime, ALLOWED_VIDEO_TYPES[mime]

    received = content_type or "unknown"
    allowed = ", ".join(sorted(ALLOWED_VIDEO_TYPES))
    raise ValueError(
        f"Unsupported video format ({received}). "
        f"Allowed types: {allowed}. "
        "Record in the browser (WebM/MP4) or upload a local .mp4 / .webm / .mov file."
    )


def resolve_video_content_type_from_metadata(
    content_type: str | None,
    filename: str | None,
) -> tuple[str, str]:
    """
    Resolve MIME/extension from headers/filename only (no file bytes).

    Used for direct-to-GCS signed uploads where the backend never sees the body.
    """
    return resolve_video_content_type(content_type, filename, b"")
