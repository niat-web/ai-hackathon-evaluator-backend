"""Hackathon card banners: small WebP uploads and reusable signed URLs."""

import io
import random
from datetime import timedelta
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

from app.services.hackathon_service import HackathonService
from app.utils.banner_cache import banner_signed_url_is_fresh
from app.utils.image_upload import prepare_card_banner
from app.utils.time import now_ist


def _png(width: int, height: int) -> bytes:
    """Noisy PNG so a resized WebP is actually smaller than the upload."""
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    rng = random.Random(1)
    for x in range(0, width, 20):
        color = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        draw.rectangle([x, 0, x + 20, height], fill=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_prepare_card_banner_shrinks_wide_png_to_webp():
    original = _png(3200, 800)
    payload, content_type, extension = prepare_card_banner(original, "image/png")
    assert content_type == "image/webp"
    assert extension == ".webp"
    assert len(payload) < len(original)
    with Image.open(io.BytesIO(payload)) as image:
        assert image.size[0] <= 1600
        assert image.format == "WEBP"


def test_prepare_card_banner_keeps_animated_gif():
    first = Image.new("RGB", (8, 8), (255, 0, 0))
    second = Image.new("RGB", (8, 8), (0, 0, 255))
    buffer = io.BytesIO()
    first.save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=[second],
        duration=200,
        loop=0,
    )
    original = buffer.getvalue()
    payload, content_type, extension = prepare_card_banner(original, "image/gif")
    assert payload == original
    assert content_type == "image/gif"
    assert extension == ".gif"


def test_fresh_signed_url_is_reused_without_signing():
    expires = (now_ist() + timedelta(days=3)).isoformat()
    assert banner_signed_url_is_fresh(expires) is True
    assert banner_signed_url_is_fresh(None) is False
    stale = (now_ist() + timedelta(hours=1)).isoformat()
    assert banner_signed_url_is_fresh(stale) is False

    service = HackathonService(firebase=MagicMock())
    data = {
        "id": "h1",
        "banner_path": "gs://bucket/hackathons/h1/banners/old.webp",
        "banner_signed_url": "https://cdn.example/banner",
        "banner_signed_url_expires_at": expires,
    }
    with patch("app.services.hackathon_service.sign_banner_url") as sign:
        url = service.attach_banner_url(data, collection="hackathons", document_id="h1")
    assert url == "https://cdn.example/banner"
    sign.assert_not_called()
    service.firebase.update_document.assert_not_called()


def test_stale_signed_url_is_resigned_and_stored():
    service = HackathonService(firebase=MagicMock())
    data = {
        "id": "h1",
        "banner_path": "gs://bucket/hackathons/h1/banners/card.webp",
        "banner_signed_url": "https://cdn.example/expired",
        "banner_signed_url_expires_at": (now_ist() - timedelta(hours=1)).isoformat(),
    }
    with patch(
        "app.services.hackathon_service.sign_banner_url",
        return_value=("https://cdn.example/new", "2099-01-01T00:00:00+05:30"),
    ) as sign:
        url = service.attach_banner_url(data, collection="hackathons", document_id="h1")
    assert url == "https://cdn.example/new"
    sign.assert_called_once()
    service.firebase.update_document.assert_called_once_with(
        "hackathons",
        "h1",
        {
            "banner_signed_url": "https://cdn.example/new",
            "banner_signed_url_expires_at": "2099-01-01T00:00:00+05:30",
        },
    )


def test_upload_banner_stores_versioned_webp_with_cache_control():
    service = HackathonService(firebase=MagicMock(), storage_client=MagicMock())
    service.bucket_name = "hack-bucket"
    blob = MagicMock()
    service.storage_client.bucket.return_value.blob.return_value = blob

    path = service._upload_banner("h1", ("banner.png", _png(2000, 600), "image/png"))

    assert path.startswith("gs://hack-bucket/hackathons/h1/banners/")
    assert path.endswith(".webp")
    assert blob.cache_control == "public, max-age=432000"
    uploaded = blob.upload_from_string.call_args
    assert uploaded.kwargs["content_type"] == "image/webp"
    assert len(uploaded.args[0]) < 200_000
