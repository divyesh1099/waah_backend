# app/utils/media.py
from __future__ import annotations
import os
import re
import uuid
from pathlib import Path
from typing import Iterable
from fastapi import UploadFile, HTTPException
from app.config import Settings as settings
_ALLOWED_IMAGE_CT: set[str] = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"
}

_slugify_re = re.compile(r"[^a-zA-Z0-9_.-]+")

def _slugify(name: str) -> str:
    # keep extension; clean base
    base, dot, ext = name.rpartition('.')
    base = base or name  # if no dot
    safe_base = _slugify_re.sub('-', base).strip('-') or 'file'
    if dot:
        ext = _slugify_re.sub('', ext)
        return f"{safe_base}.{ext}" if ext else safe_base
    return safe_base

async def save_image_upload(
    file: UploadFile,
    *,
    subdir: str,
    allowed_types: Iterable[str] | None = None,
    max_bytes: int = 8 * 1024 * 1024,  # 8 MB
) -> str:
    """Save `UploadFile` under MEDIA_ROOT/<subdir>/ and return the **URL path**.
    Raises HTTPException on invalid content-type or size.
    """
    ct = (file.content_type or '').lower()
    allowed = set(allowed_types or _ALLOWED_IMAGE_CT)
    if ct not in allowed:
        raise HTTPException(status_code=415, detail=f"Unsupported image type: {ct}")

    # simple size guard (if client sent content-length)
    try:
        size_hdr = file.headers.get('content-length') if file.headers else None
        if size_hdr and int(size_hdr) > max_bytes:
            raise HTTPException(status_code=413, detail="Image too large")
    except Exception:
        pass

    folder = (settings.MEDIA_ROOT / subdir).resolve()
    folder.mkdir(parents=True, exist_ok=True)

    original = _slugify(file.filename or 'image')
    unique = uuid.uuid4().hex[:8]
    # try to keep correct extension
    if '.' in original:
        base, _, ext = original.rpartition('.')
        fname = f"{base}-{unique}.{ext}"
    else:
        # map by content type if no extension
        ext_map = {
            'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif',
            'image/webp': 'webp', 'image/svg+xml': 'svg'
        }
        ext = ext_map.get(ct, 'img')
        fname = f"{original}-{unique}.{ext}"

    dest = folder / fname

    # stream to disk
    with dest.open('wb') as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)

    # return URL path for StaticFiles mount
    rel = dest.relative_to(settings.MEDIA_ROOT).as_posix()
    return f"{settings.MEDIA_URL}/{rel}"