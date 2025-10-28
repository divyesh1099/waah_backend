# app/routers/media.py
from __future__ import annotations
import re
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    Query,
)
from app.util.media import save_image_upload

# IMPORTANT: prefix is now "/media" not "/api/media"
router = APIRouter(prefix="/media", tags=["media"])

# allow only sane subdir names to avoid path traversal
_SUBDIR_RE = re.compile(r"^[a-zA-Z0-9_/-]{1,64}$")

@router.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    subdir: str = Query(
        "items",
        description="Folder under MEDIA_ROOT where we'll store this file",
    ),
):
    """
    Matches ApiClient.uploadMedia():
    - accepts multipart/form-data with 'file'
    - responds with {"path": "<public url-ish path>"}
    """
    if not _SUBDIR_RE.match(subdir):
        raise HTTPException(status_code=400, detail="Invalid subdir")

    # save_image_upload() should write the file and return something like
    # "/media/items/<generated>.jpg"
    url_path = await save_image_upload(file, subdir=subdir)

    # Flutter expects 'path'
    return {"path": url_path}
