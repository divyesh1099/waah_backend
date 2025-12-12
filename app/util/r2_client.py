from __future__ import annotations
import uuid
from datetime import datetime
from typing import IO, Optional

import boto3
from fastapi.concurrency import run_in_threadpool

from app.config import settings


def is_enabled() -> bool:
    """Return True if all required R2 env vars are present."""
    return all([
        settings.R2_ACCOUNT_ID,
        settings.R2_BUCKET_NAME,
        settings.R2_ACCESS_KEY_ID,
        settings.R2_SECRET_ACCESS_KEY,
    ])



# Global client cache
_client_instance = None

def _client():
    """Lazily construct the R2 S3-compatible client with caching."""
    global _client_instance
    
    if not is_enabled():
        raise RuntimeError("R2 is not configured")
        
    if _client_instance:
        return _client_instance

    session = boto3.session.Session()
    _client_instance = session.client(
        service_name="s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
    )
    return _client_instance


def _safe_ext(name: str) -> str:
    lower = name.lower()
    if "." in lower:
        return lower.rsplit(".", 1)[-1]
    return ""


def generate_key(filename: str, subdir: str) -> str:
    """Organise uploads by date and subdir, keep keys unique."""
    today = datetime.utcnow().strftime("%Y/%m/%d")
    ext = _safe_ext(filename)
    unique = uuid.uuid4().hex
    if ext:
        return f"{subdir}/{today}/{unique}.{ext}"
    return f"{subdir}/{today}/{unique}"


async def upload_fileobj(
    *,
    fileobj: IO[bytes],
    filename: str,
    content_type: Optional[str],
    subdir: str,
) -> str:
    """
    Upload a file-like object to R2 and return the public URL or signed-style URL
    based on provided base.
    """
    if not is_enabled():
        raise RuntimeError("R2 is not configured")

    key = generate_key(filename, subdir=subdir)

    extra_args = {}
    if content_type:
        extra_args["ContentType"] = content_type

    # boto3 is blocking; run it in a thread to avoid blocking the event loop.
    await run_in_threadpool(
        lambda: _client().upload_fileobj(
            Fileobj=fileobj,
            Bucket=settings.R2_BUCKET_NAME,
            Key=key,
            ExtraArgs=extra_args or None,
        )
    )

    base = settings.R2_PUBLIC_BASE_URL
    if base:
        return f"{base.rstrip('/')}/{key}"
    # fallback S3-style URL (bucket visibility dictates accessibility)
    return f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com/{settings.R2_BUCKET_NAME}/{key}"


async def check_health() -> dict:
    """
    Lightweight health check for R2 config/credentials.
    Tries a HEAD Bucket; does not list or create objects.
    """
    if not is_enabled():
        return {"enabled": False, "ok": False, "error": "R2 not configured"}

    def _head_bucket():
        return _client().head_bucket(Bucket=settings.R2_BUCKET_NAME)

    try:
        await run_in_threadpool(_head_bucket)
        return {
            "enabled": True,
            "ok": True,
            "bucket": settings.R2_BUCKET_NAME,
            "account": settings.R2_ACCOUNT_ID,
            "public_base_url": settings.R2_PUBLIC_BASE_URL,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "ok": False,
            "error": str(exc),
            "bucket": settings.R2_BUCKET_NAME,
            "account": settings.R2_ACCOUNT_ID,
        }
