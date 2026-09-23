"""
Stable hackathon-banner URLs.

Card images are public marketing assets stored beside private videos, so the
bucket stays private. A fresh v4 signature on every list response changes the
URL, which forces the browser to download the file again and adds an IAM
signBlob call per card before the image can start. URLs are reused until they
are close to GCS's 7-day maximum, and objects are stored with Cache-Control
so the browser keeps the bytes.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from google.cloud import storage

from app.utils.gcs_video import generate_signed_url, parse_gs_uri
from app.utils.time import now_ist, parse_to_ist


logger = logging.getLogger(__name__)

# GCS v4 signed URLs cannot live longer than 7 days.
BANNER_SIGNED_URL_TTL_SECONDS = 7 * 24 * 60 * 60
# Resign before expiry so a card never receives a URL that dies mid-session.
BANNER_URL_REFRESH_BEFORE_SECONDS = 36 * 60 * 60
# Shorter than the reuse window so a cached response expires while the URL
# is still valid.
BANNER_CACHE_CONTROL = "public, max-age=432000"


def banner_signed_url_is_fresh(expires_at: str | None) -> bool:
    """True when a stored signed URL should be reused without calling GCS."""
    if not expires_at:
        return False
    try:
        expiry = parse_to_ist(expires_at)
    except (TypeError, ValueError):
        return False
    remaining = expiry - now_ist()
    return remaining > timedelta(seconds=BANNER_URL_REFRESH_BEFORE_SECONDS)


def sign_banner_url(client: storage.Client, gs_uri: str) -> tuple[str, str] | None:
    """
    Ensure the object is browser-cacheable, then sign it for 7 days.

    Returns ``(url, expires_at_iso)`` or None when signing fails.
    """
    _ensure_banner_cache_control(client, gs_uri)
    url = generate_signed_url(
        client,
        gs_uri,
        expiry_seconds=BANNER_SIGNED_URL_TTL_SECONDS,
    )
    if not url:
        return None
    expires_at = (now_ist() + timedelta(seconds=BANNER_SIGNED_URL_TTL_SECONDS)).isoformat()
    return url, expires_at


def _ensure_banner_cache_control(client: storage.Client, gs_uri: str) -> None:
    """Set long-lived cache metadata. Failure still allows a signed URL."""
    try:
        bucket_name, object_name = parse_gs_uri(gs_uri)
        blob = client.bucket(bucket_name).blob(object_name)
        blob.cache_control = BANNER_CACHE_CONTROL
        blob.patch()
    except Exception:
        logger.warning("Could not set banner cache headers for %s", gs_uri)
