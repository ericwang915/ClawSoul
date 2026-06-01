"""
Tigris object storage — upload generated photos so the web dashboard
(running on a separate Fly machine with its own /data volume) can serve
them in the Memory Gallery.

Tigris is Fly's S3-compatible object store; we use boto3 against the
endpoint Fly provisions for the bucket (``fly storage create``).  The
required env vars come from Fly's auto-secret injection:

    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
    AWS_ENDPOINT_URL_S3      (e.g. https://fly.storage.tigris.dev)
    AWS_REGION               (typically "auto")
    BUCKET_NAME              (the bucket Fly created)

If any of these are missing the helpers degrade to no-ops — the worker
keeps writing photos to local /data as before; only the cross-machine
gallery feature is lost.  This keeps single-tenant / dev environments
unaffected by Tigris config.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)


_client_lock = threading.Lock()
_client: Any | None = None


def _bucket() -> str:
    return os.environ.get("BUCKET_NAME", "").strip()


def is_configured() -> bool:
    return bool(
        os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
        and os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
        and os.environ.get("AWS_ENDPOINT_URL_S3", "").strip()
        and _bucket()
    )


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        try:
            import boto3  # local import — only needed when Tigris is on
            from botocore.config import Config
        except ImportError as exc:
            logger.warning("[tigris] boto3 not installed: %s", exc)
            return None
        _client = boto3.client(
            "s3",
            endpoint_url=os.environ["AWS_ENDPOINT_URL_S3"],
            region_name=os.environ.get("AWS_REGION", "auto"),
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            # Tigris uses path-style; virtual-hosted fails on the .dev domain.
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        return _client


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
    """Upload raw bytes to ``key``. Best-effort; returns success."""
    if not is_configured():
        return False
    client = _get_client()
    if client is None:
        return False
    try:
        client.put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=content_type)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[tigris] put_bytes failed key=%s: %s", key, exc)
        return False


def get_bytes(key: str) -> bytes | None:
    """Download ``key`` as bytes, or ``None`` if missing / on error."""
    if not is_configured():
        return None
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.get_object(Bucket=_bucket(), Key=key)
        return resp["Body"].read()
    except Exception as exc:  # noqa: BLE001  (NoSuchKey included — treat as absent)
        logger.debug("[tigris] get_bytes miss/err key=%s: %s", key, exc)
        return None


def delete_key(key: str) -> bool:
    """Delete a single object by key. Best-effort; returns success."""
    if not is_configured():
        return False
    client = _get_client()
    if client is None:
        return False
    try:
        client.delete_object(Bucket=_bucket(), Key=key)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[tigris] delete_key failed key=%s: %s", key, exc)
        return False


def object_key(user_id: str, filename: str) -> str:
    """Stable key layout: ``users/<uid>/<basename>``."""
    base = os.path.basename(filename)
    return f"users/{user_id}/{base}"


def upload_photo(user_id: str, local_path: str, filename: str | None = None,
                 content_type: str = "image/jpeg") -> str | None:
    """Upload a local file to Tigris under ``users/<uid>/<filename>``.

    Returns the object key on success, ``None`` if Tigris isn't
    configured or the upload errored (logged).  Photo generation
    continues regardless — gallery is best-effort.
    """
    if not is_configured():
        return None
    client = _get_client()
    if client is None:
        return None
    name = filename or os.path.basename(local_path)
    key = object_key(user_id, name)
    try:
        with open(local_path, "rb") as f:
            client.put_object(
                Bucket=_bucket(), Key=key, Body=f.read(),
                ContentType=content_type,
            )
        return key
    except Exception as exc:
        logger.warning("[tigris] upload failed key=%s: %s", key, exc)
        return None


def presign_get(key: str, expires_sec: int = 900) -> str | None:
    """Return a presigned GET URL for ``key``, or ``None`` on error.

    Default expiry is 15 minutes — short enough to limit the blast
    radius of a leaked URL (logs, screenshots, page-source sharing)
    while leaving enough time for a user to scroll the gallery and
    open the lightbox without the photos blanking out.  The web
    refreshes URLs whenever ``/api/sanctum/photos`` is called again.
    """
    if not is_configured():
        return None
    client = _get_client()
    if client is None:
        return None
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": _bucket(), "Key": key},
            ExpiresIn=max(60, int(expires_sec)),
        )
    except Exception as exc:
        logger.warning("[tigris] presign failed key=%s: %s", key, exc)
        return None


def delete_user_objects(user_id: str) -> int:
    """Delete every object under ``users/<user_id>/`` in the bucket.

    Used during account deletion — Pg ``photos`` rows cascade with
    ``auth.users``, but Tigris objects don't, so without this call
    photos linger in storage forever after the account is gone.

    Returns the number of objects removed (0 if Tigris isn't
    configured / the prefix was already empty).  Best-effort: errors
    are logged, not raised, so the destroy flow can continue.
    """
    if not is_configured():
        return 0
    client = _get_client()
    if client is None:
        return 0
    prefix = f"users/{user_id}/"
    bucket = _bucket()
    removed = 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in (page.get("Contents") or [])]
            if not keys:
                continue
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            removed += len(keys)
    except Exception as exc:
        logger.warning("[tigris] delete user=%s prefix=%s failed: %s",
                       user_id[:8], prefix, exc)
    return removed


__all__ = ["is_configured", "object_key", "upload_photo", "presign_get",
           "delete_user_objects", "delete_key", "put_bytes", "get_bytes"]
