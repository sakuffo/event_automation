"""Google Drive download and Wix upload helpers."""

from __future__ import annotations

import os
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

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


def upload_image_to_wix(image_url: str, event_name: str, runtime: SyncRuntime) -> Optional[Dict[str, Any]]:
    if not image_url:
        return None

    try:
        file_id = extract_google_drive_file_id(image_url)
        if not file_id:
            logger.warning("⚠️  Invalid Google Drive URL: %s", image_url)
            return None

        cached_media = runtime.get_cached_wix_media(file_id)
        if cached_media is not None:
            runtime.record_wix_hit()
            logger.info("♻️  Reusing cached Wix media for: %s", event_name)
            return cached_media

        logger.info("📥 Downloading image from Google Drive for: %s", event_name)
        image_data, filename, mime_type = download_from_google_drive(file_id, runtime)
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

        if descriptor and file_id:
            runtime.record_wix_upload()
            runtime.cache_wix_media(file_id, descriptor)

        logger.info("✅ Uploaded image for: %s", event_name)
        return descriptor

    except Exception as exc:
        logger.error("⚠️  Failed to upload image for %s: %s", event_name, exc)
        return None


