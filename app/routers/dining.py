# app/routers/dining.py
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_auth
from app.models.core import DiningTable  # this is the model name used in Phase-1

router = APIRouter(prefix="/dining", tags=["dining"])

@router.post("/tables")
def create_table(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    # Only keep fields that actually exist on the model
    model_cols = set(DiningTable.__table__.columns.keys())  # e.g. {'id','branch_id','code','zone','seats',...}
    payload = {k: v for k, v in body.items() if k in model_cols}

    # Requireds / defaults (align with your schema naming)
    if not payload.get("code"):
        raise HTTPException(400, detail="code is required")
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
    return {"id": t.id}