# app/routers/identity.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app.db import get_db
from app.deps import require_auth
from app.models.core import Branch, User

router = APIRouter(prefix="/identity", tags=["identity"])

@router.get("/branches", summary="List branches I can work on")
def list_branches(
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    Frontend calls this to populate the Branch picker.

    If tenant_id is not provided, infer it from the logged-in user.
    """
    me = db.get(User, sub)
    if not me:
        return []

    tid = tenant_id or me.tenant_id

    rows: List[Branch] = (
        db.query(Branch)
        .filter(Branch.tenant_id == tid)
        .order_by(Branch.created_at.asc())
        .all()
    )

    out = []
    for b in rows:
        out.append(
            {
                "id": b.id,
                "tenant_id": b.tenant_id,
                "name": b.name,
                "phone": b.phone,
                "gstin": b.gstin,
                "address": b.address,
                "state_code": b.state_code,
            }
        )
    return out
