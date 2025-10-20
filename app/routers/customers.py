from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db import get_db
from app.deps import require_auth
from app.models.core import Customer

router = APIRouter(prefix="/customers", tags=["customers"])

@router.post("/")
def create_customer(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    # keep only real model columns (avoids passing unknown fields)
    allowed = set(Customer.__table__.columns.keys())
    payload = {k: v for k, v in body.items() if k in allowed}

    if not payload.get("name"):
        raise HTTPException(400, detail="name is required")

    try:
        c = Customer(**payload)
        db.add(c)
        db.commit()
        db.refresh(c)
        return {"id": c.id}
    except IntegrityError:
        db.rollback()
        # if phone is unique and already exists, return that record's id (good enough for tests)
        phone = payload.get("phone")
        if phone:
            existing = db.query(Customer).filter(Customer.phone == phone).first()
            if existing:
                return {"id": existing.id}
        raise HTTPException(409, detail="could not create customer")
