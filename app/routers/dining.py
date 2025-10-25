# app/routers/dining.py
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.db import get_db
from app.deps import require_auth, require_perm
from app.models.core import DiningTable  # model with IdMixin/TSMMixin

router = APIRouter(prefix="/dining", tags=["dining"])


def _row_from_table(t: DiningTable) -> dict:
    """
    Shape this exactly like Flutter's DiningTable.fromJson() expects:
      {
        "id": "...",
        "branch_id": "...",
        "code": "...",
        "zone": "...",
        "seats": 4
      }
    """
    return {
        "id": t.id,
        "branch_id": t.branch_id,
        "code": t.code,
        "zone": t.zone,
        "seats": t.seats,
    }


# ------------------------------------------------------------------
# POST /dining/tables  -> create new table
# ------------------------------------------------------------------
@router.post("/tables")
def create_table(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    body can include:
      branch_id: str   (required by model)
      code: str        (required, unique-ish)
      zone: str | None
      seats: int | None

    We only keep keys that actually exist on DiningTable.
    """

    model_cols = set(DiningTable.__table__.columns.keys())
    payload = {k: v for k, v in body.items() if k in model_cols}

    if not payload.get("code"):
        raise HTTPException(400, detail="code is required")

    # seat default if column exists
    if "seats" in model_cols:
        payload.setdefault("seats", 2)

    try:
        t = DiningTable(**payload)
        db.add(t)
        db.commit()
        db.refresh(t)
    except IntegrityError:
        db.rollback()
        # helpful if (branch_id, code) is unique
        raise HTTPException(409, detail="table with this code already exists")

    return _row_from_table(t)


# ------------------------------------------------------------------
# GET /dining/tables  -> list tables for a branch
# ------------------------------------------------------------------
@router.get("/tables")
def list_tables(
    branch_id: Optional[str] = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    Returns active (not soft-deleted) dining tables.

    Query params:
      ?branch_id=BRANCH123   (optional, but frontend will send '' for now)

    Flutter expects a plain List[ {id, branch_id, code, zone, seats} ].
    """

    q = db.query(DiningTable)

    # If TSMMixin gave you deleted_at, hide soft-deleted
    if hasattr(DiningTable, "deleted_at"):
        q = q.filter(DiningTable.deleted_at.is_(None))

    if branch_id is not None:
        q = q.filter(DiningTable.branch_id == branch_id)

    rows: List[DiningTable] = (
        q.order_by(DiningTable.code.asc())  # nice stable ordering
        .all()
    )

    return [_row_from_table(t) for t in rows]


# ------------------------------------------------------------------
# DELETE /dining/tables/{table_id}  -> soft delete table
# ------------------------------------------------------------------
@router.delete("/tables/{table_id}")
def delete_table(
    table_id: str,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Soft-delete a table by stamping deleted_at.
    This mirrors how we soft-delete categories/items elsewhere.
    Frontend can call this if you add `catalogRepo.deleteTable(...)` later.
    """

    t: DiningTable | None = db.get(DiningTable, table_id)
    if not t:
        raise HTTPException(status_code=404, detail="table not found")

    # already deleted? we'll just 404 to match category behavior
    if hasattr(t, "deleted_at") and getattr(t, "deleted_at") is not None:
        raise HTTPException(status_code=404, detail="table not found")

    # mark soft delete if model supports it, else hard delete
    if hasattr(t, "deleted_at"):
        setattr(t, "deleted_at", datetime.utcnow())
        db.commit()
    else:
        db.delete(t)
        db.commit()

    return {"ok": True, "id": table_id}
