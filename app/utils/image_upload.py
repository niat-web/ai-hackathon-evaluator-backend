"""
Image upload type detection and card-sized encoding for hackathon banners.
"""

import io
import logging
from pathlib import Path

from PIL import Image, UnidentifiedImageError


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

# Card art is shown around 800px CSS wide. 1600px covers a 2x display.
CARD_BANNER_MAX_EDGE = 1600
CARD_BANNER_WEBP_QUALITY = 80

logger = logging.getLogger(__name__)


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
    raise ValueError(f"Unsupported image format ({received}). Allowed types: {allowed}.")


def prepare_card_banner(payload: bytes, content_type: str) -> tuple[bytes, str, str]:
    """
    Shrink a banner for hackathon cards.

    Returns WebP bytes capped at 1600px on the long edge. Animated GIF/WebP
    is kept as uploaded so motion is not flattened. If encoding fails or does
    not get smaller, the original bytes are returned.
    """
    extension = ALLOWED_IMAGE_TYPES[content_type]
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
                return payload, content_type, extension
            original_size = image.size
            prepared = _flatten_for_webp(image)
            prepared.thumbnail(
                (CARD_BANNER_MAX_EDGE, CARD_BANNER_MAX_EDGE),
                Image.Resampling.LANCZOS,
            )
            resized = prepared.size != original_size
            encoded = io.BytesIO()
            prepared.save(
                encoded,
                format="WEBP",
                quality=CARD_BANNER_WEBP_QUALITY,
                method=4,
            )
    except (UnidentifiedImageError, OSError, ValueError):
        logger.warning("Banner card encode failed; storing original upload")
        return payload, content_type, extension

    webp = encoded.getvalue()
    if not webp:
        return payload, content_type, extension
    # Keep an already-small original when WebP does not shrink it. A downscale
    # is kept even if the byte size is close, because the card decodes fewer pixels.
    if len(webp) >= len(payload) and not resized:
        return payload, content_type, extension
    return webp, "image/webp", ".webp"


def _flatten_for_webp(image: Image.Image) -> Image.Image:
    """Convert modes WebP can store without pulling in extra frames."""
    if image.mode in ("RGBA", "LA"):
        return image.convert("RGBA")
    if image.mode == "P" and "transparency" in image.info:
        return image.convert("RGBA")
    if image.mode != "RGB":
        return image.convert("RGB")
    return image.copy()
