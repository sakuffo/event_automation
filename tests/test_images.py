"""Tests for image processing utilities."""

from io import BytesIO

import pytest
from PIL import Image

import event_sync.images as images


def _generate_image_bytes(size=(200, 200), color=(255, 0, 0)) -> bytes:
    image = Image.new("RGB", size, color=color)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def test_prepare_image_returns_same_payload_when_within_limit():
    img_bytes = _generate_image_bytes()
    processed, filename, mime_type, resized = images.prepare_image_for_wix(
        img_bytes, "test.jpg", "image/jpeg"
    )
    assert processed == img_bytes
    assert filename == "test.jpg"
    assert mime_type == "image/jpeg"
    assert resized is False


def test_prepare_image_compresses_when_overridden_limit(monkeypatch):
    img_bytes = _generate_image_bytes(size=(800, 800))
    monkeypatch.setattr(images, "MAX_WIX_IMAGE_BYTES", len(img_bytes) // 4)

    processed, filename, mime_type, resized = images.prepare_image_for_wix(
        img_bytes, "large.png", "image/png"
    )

    assert processed is not None
    assert len(processed) < len(img_bytes)
    assert filename == "large.jpg"  # converted to jpeg
    assert mime_type == "image/jpeg"
    assert resized is True


