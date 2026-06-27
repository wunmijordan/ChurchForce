"""
core/storage.py

Environment-aware file storage for ChurchForce.

Dev  (DEBUG=True)  → files go to MEDIA_ROOT (mediafiles/) via Django's
                     FileSystemStorage.  No Cloudinary account needed.

Prod (DEBUG=False) → files go to Cloudinary via
                     cloudinary_storage.storage.MediaCloudinaryStorage.

Usage
-----
All models that previously used CloudinaryField directly now use
SmartFileField (for non-audio generic files) or the playback_url
property helpers defined here.

Why not swap CloudinaryField for FileField in models?
-----------------------------------------------------
Changing the field class would generate new migrations and alter
the DB column type.  Instead we keep CloudinaryField in model
definitions (it stores a plain varchar in the DB — the public_id
or local path) and control *how* that value is turned into a URL
via smart_url() below.  Migrations are unaffected.

Helpers
-------
smart_url(field_value, resource_type='image')
    Turn a CloudinaryField raw value into the correct URL for the
    current environment.  In dev, treats the value as a relative
    path under MEDIA_ROOT and returns /media/<value>.  In prod,
    calls Cloudinary's url() builder.

smart_upload(file_obj, folder, resource_type='auto')
    Upload a file to the right backend and return
    {'path': <storage key>, 'url': <public url>}.
    In dev: saves to default_storage (FileSystemStorage → mediafiles/).
    In prod: uploads to Cloudinary.

These are used by every view that previously wrote:
    if not settings.DEBUG:
        cloudinary.uploader.upload(...)
    else:
        default_storage.save(...)
"""

import logging
import os
import mimetypes

from django.conf import settings

logger = logging.getLogger(__name__)

# ── Environment flag ──────────────────────────────────────────────────────────

IS_PROD = not settings.DEBUG


# ── URL resolution ────────────────────────────────────────────────────────────


def smart_url(field_value, resource_type: str = "image") -> str:
    """
    Resolve a CloudinaryField raw value to a usable URL.

    Parameters
    ----------
    field_value   : the raw value of a CloudinaryField (public_id in prod,
                    relative path in dev), or the field object itself.
    resource_type : 'image' | 'video' | 'raw'  (only used in prod)

    Returns '' for blank/None values instead of a broken Cloudinary URL.
    """
    # Accept the field object or its string representation
    raw = str(field_value) if field_value else ""
    if not raw or raw in ("None", "none", ""):
        return ""

    if IS_PROD:
        try:
            import cloudinary

            return cloudinary.CloudinaryResource(raw, resource_type=resource_type).url
        except Exception as exc:
            logger.warning(
                "smart_url: Cloudinary URL build failed for %r: %s", raw, exc
            )
            return ""
    else:
        # In dev, raw is either a relative path stored by FileSystemStorage
        # (e.g. "music/stems/my_track.mp3") or a full path.
        # Strip any leading slash and serve from MEDIA_URL.
        path = raw.lstrip("/")
        media_url = getattr(settings, "MEDIA_URL", "/media/").rstrip("/")
        return f"{media_url}/{path}"


def smart_audio_url(field_value) -> str:
    """Convenience wrapper for audio/video CloudinaryFields."""
    return smart_url(field_value, resource_type="video")


def smart_image_url(field_value) -> str:
    """Convenience wrapper for image CloudinaryFields."""
    return smart_url(field_value, resource_type="image")


def smart_raw_url(field_value) -> str:
    """Convenience wrapper for raw (PDF, document) CloudinaryFields."""
    return smart_url(field_value, resource_type="raw")


# ── Upload ────────────────────────────────────────────────────────────────────


def smart_upload(
    file_obj,
    folder: str,
    resource_type: str = "auto",
    public_id: str | None = None,
) -> dict:
    """
    Upload *file_obj* to Cloudinary (prod) or mediafiles/ (dev).

    Returns
    -------
    {
        'path':         str,   # storage key / public_id — store on model
        'url':          str,   # public URL to serve
        'original_name': str,  # original filename
    }

    Raises
    ------
    StorageError on any upload failure.
    """
    original_name = getattr(file_obj, "name", "") or ""

    if IS_PROD:
        try:
            import cloudinary.uploader

            kwargs = {
                "folder": folder,
                "resource_type": resource_type,
            }
            if public_id:
                kwargs["public_id"] = public_id

            result = cloudinary.uploader.upload(file_obj, **kwargs)
            return {
                "path": result["public_id"],
                "url": result["secure_url"],
                "original_name": result.get("original_filename") or original_name,
            }
        except Exception as exc:
            raise StorageError(f"Cloudinary upload failed: {exc}") from exc
    else:
        from django.core.files.storage import default_storage

        # Build a clean relative path: folder/filename
        filename = os.path.basename(original_name) or "upload"
        relative = f"{folder.strip('/')}/{filename}"
        try:
            saved = default_storage.save(relative, file_obj)
            # default_storage.url() returns /media/<path>
            url = default_storage.url(saved)
            return {
                "path": saved,
                "url": url,
                "original_name": original_name,
            }
        except Exception as exc:
            raise StorageError(f"Local storage save failed: {exc}") from exc


class StorageError(Exception):
    """Raised when smart_upload fails."""

    pass


# ── Cloudinary-field truthiness helper ───────────────────────────────────────


def field_has_value(cloudinary_field) -> bool:
    """
    Return True only when a CloudinaryField actually contains a stored file.

    CloudinaryField is a string subclass — even when blank it evaluates
    as truthy (non-empty string), which causes .url to be called on a
    field that has no file, producing a garbage Cloudinary URL.

    Use this instead of `if model.audio_file:`.
    """
    raw = str(cloudinary_field) if cloudinary_field else ""
    return bool(raw and raw not in ("None", "none", ""))
