"""Image download (Google Drive or HTTP) and Wix upload helpers."""

from __future__ import annotations

import os
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

import requests

from .constants import MAX_WIX_IMAGE_BYTES
from .logging_utils import get_logger
from .runtime import SyncRuntime
from .utils import extract_google_drive_file_id


logger = get_logger(__name__)


def download_from_google_drive(file_id: str, runtime: SyncRuntime) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    try:
        cached = runtime.get_cached_drive_file(file_id)
        if cached is not None:
            runtime.record_drive_hit()
            return cached

        runtime.record_drive_miss()
        drive_service = runtime.get_drive_service()

        metadata = (
            drive_service.files()
            .get(fileId=file_id, fields="name,mimeType")
            .execute()
        )

        request = drive_service.files().get_media(fileId=file_id)
        file_data = BytesIO()
        file_data.write(request.execute())
        file_data.seek(0)

        payload = (file_data.read(), metadata.get("name"), metadata.get("mimeType"))
        runtime.cache_drive_file(file_id, payload)
        return payload

    except Exception as exc:
        logger.error("❌ Failed to download from Google Drive: %s", exc)
        return None, None, None


def prepare_image_for_wix(
    image_data: Optional[bytes],
    filename: Optional[str],
    mime_type: Optional[str],
) -> Tuple[Optional[bytes], Optional[str], Optional[str], bool]:
    """Ensure the payload respects Wix Media limits."""

    if not image_data:
        return None, filename, mime_type, False

    if len(image_data) <= MAX_WIX_IMAGE_BYTES:
        return image_data, filename, mime_type, False

    original_mb = len(image_data) / (1024 * 1024)
    logger.info(
        "   ✂️  Image '%s' is %.1fMB (limit %.1fMB) - attempting compression",
        filename,
        original_mb,
        MAX_WIX_IMAGE_BYTES / (1024 * 1024),
    )

    try:
        from PIL import Image, ImageOps  # type: ignore
    except ImportError:
        logger.warning(
            "   ⚠️  Pillow is required to compress large images. Install it with `pip install Pillow`. Skipping image upload."
        )
        return None, filename, mime_type, False

    try:
        image = Image.open(BytesIO(image_data))
    except Exception as err:
        logger.warning("   ⚠️  Failed to read image '%s' for compression: %s", filename, err)
        return None, filename, mime_type, False

    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass

    if image.mode != "RGB":
        image = image.convert("RGB")

    stem = os.path.splitext(filename or "event_image")[0] or "event_image"
    target_filename = f"{stem}.jpg"
    scales = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]
    qualities = [90, 85, 80, 75, 70, 65, 60]

    for scale in scales:
        if scale != 1.0:
            new_width = max(1, int(image.width * scale))
            new_height = max(1, int(image.height * scale))
            candidate = image.resize((new_width, new_height), Image.LANCZOS)
        else:
            candidate = image

        for quality in qualities:
            buffer = BytesIO()
            try:
                candidate.save(buffer, format="JPEG", quality=quality, optimize=True)
            except OSError:
                buffer = BytesIO()
                candidate.save(buffer, format="JPEG", quality=quality)

            compressed_data = buffer.getvalue()
            if len(compressed_data) <= MAX_WIX_IMAGE_BYTES:
                compressed_mb = len(compressed_data) / (1024 * 1024)
                logger.info(
                    "   ✅ Compressed image to %.1fMB (scale %.2f, quality %d)",
                    compressed_mb,
                    scale,
                    quality,
                )
                return compressed_data, target_filename, "image/jpeg", True

    logger.warning(
        "   ⚠️  Unable to shrink image '%s' below Wix's %.1fMB limit",
        filename,
        MAX_WIX_IMAGE_BYTES / (1024 * 1024),
    )
    return None, filename, mime_type, False


def download_from_http(url: str) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Download an image from a plain HTTP(S) URL (e.g. wixstatic links)."""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        mime_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        filename = os.path.basename(url.split("?")[0]) or "event_image"
        return response.content, filename, mime_type
    except Exception as exc:
        logger.error("❌ Failed to download image from URL: %s", exc)
        return None, None, None


def _is_google_drive_url(image_url: str) -> bool:
    return "drive.google.com" in image_url or "docs.google.com" in image_url


_WIXSTATIC_PREFIX = "https://static.wixstatic.com/media/"


def is_wix_media_url(image_url: str) -> bool:
    """True when the URL points at media already hosted by Wix."""
    return (image_url or "").startswith(_WIXSTATIC_PREFIX)


def normalize_wix_media_url(image_url: str) -> str:
    """Strip render transforms (``/v1/fill/...``) from a wixstatic URL.

    Pulled event images carry a thumbnail transform suffix that doesn't always
    serve raw bytes; the bare ``/media/{file}`` URL always does.
    """
    if not is_wix_media_url(image_url):
        return image_url
    media_file = image_url[len(_WIXSTATIC_PREFIX):].split("/", 1)[0]
    return f"{_WIXSTATIC_PREFIX}{media_file}" if media_file else image_url


def upload_image_to_wix(image_url: str, event_name: str, runtime: SyncRuntime) -> Optional[Dict[str, Any]]:
    """Upload an event image to Wix Media from a Drive link or plain URL.

    Google Drive links use the Drive API (service-account auth); any other
    http(s) URL is fetched directly. Bare Drive file ids are also accepted.
    """
    if not image_url:
        return None

    try:
        image_url = normalize_wix_media_url(image_url)
        is_http = image_url.startswith("http://") or image_url.startswith("https://")
        file_id: Optional[str] = None
        if not is_http or _is_google_drive_url(image_url):
            file_id = extract_google_drive_file_id(image_url)

        # Cache key: Drive file id when available, else the URL itself.
        cache_key = file_id or image_url

        cached_media = runtime.get_cached_wix_media(cache_key)
        if cached_media is not None:
            runtime.record_wix_hit()
            logger.info("♻️  Reusing cached Wix media for: %s", event_name)
            return cached_media

        if file_id:
            logger.info("📥 Downloading image from Google Drive for: %s", event_name)
            image_data, filename, mime_type = download_from_google_drive(file_id, runtime)
        elif is_http:
            logger.info("📥 Downloading image from URL for: %s", event_name)
            image_data, filename, mime_type = download_from_http(image_url)
        else:
            logger.warning("⚠️  Unrecognized image reference: %s", image_url)
            return None

        if not image_data:
            logger.warning("⚠️  Failed to download image for: %s", event_name)
            return None

        if not mime_type or not mime_type.startswith("image/"):
            logger.warning("⚠️  Unsupported file type: %s", mime_type)
            return None

        prepared, filename, mime_type, resized = prepare_image_for_wix(
            image_data, filename, mime_type
        )
        if prepared is None:
            logger.warning(
                "⚠️  Image for '%s' exceeds Wix limits even after compression. Skipping.",
                event_name,
            )
            return None

        if resized:
            logger.info("   ✨ Using optimized image '%s' for upload", filename)

        from typing import cast

        prepared_bytes = cast(bytes, prepared)
        client = runtime.get_wix_client()
        descriptor = client.upload_image(prepared_bytes, filename, mime_type)

        if descriptor:
            runtime.record_wix_upload()
            runtime.cache_wix_media(cache_key, descriptor)

        logger.info("✅ Uploaded image for: %s", event_name)
        return descriptor

    except Exception as exc:
        logger.error("⚠️  Failed to upload image for %s: %s", event_name, exc)
        return None


