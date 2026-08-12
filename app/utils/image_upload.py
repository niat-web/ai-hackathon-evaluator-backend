"""
Image upload type detection for hackathon banners.
"""

from pathlib import Path


# MIME types accepted for hackathon banner images.
ALLOWED_IMAGE_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

MIME_ALIASES: dict[str, str] = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
}

EXTENSION_TO_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _normalize_mime(content_type: str | None) -> str | None:
    if not content_type:
        return None

    base = content_type.split(";")[0].strip().lower()
    if base in ALLOWED_IMAGE_TYPES:
        return base
    return MIME_ALIASES.get(base)


def _mime_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None

    extension = Path(filename).suffix.lower()
    return EXTENSION_TO_MIME.get(extension)


def _mime_from_magic(file_bytes: bytes) -> str | None:
    if len(file_bytes) < 12:
        return None

    header = file_bytes[:12]

    # JPEG
    if header[:3] == b"\xff\xd8\xff":
        return "image/jpeg"

    # PNG
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"

    # GIF
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"

    # WEBP (RIFF....WEBP)
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"

    return None


def resolve_image_content_type(
    content_type: str | None,
    filename: str | None,
    file_bytes: bytes,
) -> tuple[str, str]:
    """
    Resolve a supported image MIME type and file extension for a banner upload.

    Returns:
        Tuple of (content_type, extension)

    Raises:
        ValueError: If the upload cannot be recognized as a supported image.
    """
    candidates = [
        _normalize_mime(content_type),
        _mime_from_filename(filename),
        _mime_from_magic(file_bytes),
    ]

    for mime in candidates:
        if mime and mime in ALLOWED_IMAGE_TYPES:
            return mime, ALLOWED_IMAGE_TYPES[mime]

    received = content_type or "unknown"
    allowed = ", ".join(sorted(ALLOWED_IMAGE_TYPES))
    raise ValueError(
        f"Unsupported image format ({received}). Allowed types: {allowed}."
    )
