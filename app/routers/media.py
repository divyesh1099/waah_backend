from __future__ import annotations
import re
from fastapi import APIRouter, UploadFile, File, HTTPException, Query

from app.util.media import save_image_upload

router = APIRouter(prefix="/api/media", tags=["media"])

# keep subdir simple; avoid path traversal
_SUBDIR_RE = re.compile(r"^[a-zA-Z0-9_/-]{1,64}$")

@router.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    subdir: str = Query("items", description="Folder under MEDIA_ROOT"),
):
    if not _SUBDIR_RE.match(subdir):
        raise HTTPException(status_code=400, detail="Invalid subdir")
    url_path = await save_image_upload(file, subdir=subdir)
    # Return the URL path that frontend can use directly
    return {"url": url_path}
