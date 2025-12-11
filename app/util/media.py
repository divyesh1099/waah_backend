# app/util/media.py
from __future__ import annotations
import re
import uuid
from pathlib import Path
from typing import Iterable
from fastapi import UploadFile, HTTPException
from app.config import settings
from app.util import r2_client

_ALLOWED_IMAGE_CT: set[str] = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"
}

_slugify_re = re.compile(r"[^a-zA-Z0-9_.-]+")

def _slugify(name: str) -> str:
    base, dot, ext = name.rpartition(".")
    base = base or name
    safe_base = _slugify_re.sub("-", base).strip("-") or "file"
    if dot:
        ext = _slugify_re.sub("", ext)
        return f"{safe_base}.{ext}" if ext else safe_base
    return safe_base

async def _save_to_r2(file: UploadFile, *, subdir: str) -> str:
    """
    Upload to Cloudflare R2 (S3-compatible). Assumes validation already done.
    """
    # Reset pointer in case something read it earlier
    try:
        file.file.seek(0)
    except Exception:
        pass

    return await r2_client.upload_fileobj(
        fileobj=file.file,
        filename=file.filename or "upload",
        content_type=file.content_type,
        subdir=subdir,
    )


async def save_image_upload(
    file: UploadFile,
    *,
    subdir: str,
    allowed_types: Iterable[str] | None = None,
    max_bytes: int = 8 * 1024 * 1024,  # 8 MB
) -> str:
    """
    Save `UploadFile` under MEDIA_ROOT/<subdir>/ (or R2 if configured) and return the URL path.
    """
    ct = (file.content_type or "").lower()
    allowed = set(allowed_types or _ALLOWED_IMAGE_CT)
    if ct not in allowed:
        raise HTTPException(status_code=415, detail=f"Unsupported image type: {ct}")

    # Optional size guard (if client sent content-length)
    try:
        size_hdr = file.headers.get("content-length") if file.headers else None
        if size_hdr and int(size_hdr) > max_bytes:
            raise HTTPException(status_code=413, detail="Image too large")
    except Exception:
        pass

    if r2_client.is_enabled():
        return await _save_to_r2(file, subdir=subdir)

    # Fallback: local filesystem
    media_root: Path = Path(settings.MEDIA_ROOT).resolve()
    folder = (media_root / subdir).resolve()
    folder.mkdir(parents=True, exist_ok=True)

    original = _slugify(file.filename or "image")
    unique = uuid.uuid4().hex[:8]

    if "." in original:
        base, _, ext = original.rpartition(".")
        fname = f"{base}-{unique}.{ext}"
    else:
        ext_map = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/svg+xml": "svg",
        }
        ext = ext_map.get(ct, "img")
        fname = f"{original}-{unique}.{ext}"

    dest = folder / fname

    # Stream to disk
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)

    # URL to return (prefix from settings, relative path from media_root)
    rel = dest.relative_to(media_root).as_posix()
    base = "/" + settings.MEDIA_URL_BASE.strip("/")
    return f"{base}/{rel}"
