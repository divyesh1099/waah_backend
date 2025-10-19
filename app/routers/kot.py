from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from app.db import get_db
from app.models.core import KitchenTicket, KitchenTicketItem, KOTStatus
from app.deps import require_auth, require_perm
from app.util.audit import audit

router = APIRouter(prefix="/kot", tags=["kot"])

@router.post("/tickets")
def create_ticket(order_id: str, ticket_no: int, target_station: str | None = None,
                  db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    t = KitchenTicket(order_id=order_id, ticket_no=ticket_no, target_station=target_station)
    db.add(t); db.commit(); db.refresh(t)
    audit(db, sub, "kitchen_ticket", t.id, "CREATE")
    db.commit()
    return {"ticket_id": t.id}

@router.put("/tickets/{ticket_id}/status")
def update_status(ticket_id: str, status: KOTStatus, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    t = db.get(KitchenTicket, ticket_id)
    if not t:
        raise HTTPException(404, "ticket not found")
    before = {"status": t.status.value}
    t.status = status
    audit(db, sub, "kitchen_ticket", t.id, "STATUS", before=before, after={"status": status.value})
    db.commit()
    return {"ticket_id": t.id, "status": t.status.value}

@router.post("/tickets/{ticket_id}/reprint")
def reprint_ticket(ticket_id: str, reason: str = Body(..., embed=True),
                   db: Session = Depends(get_db), sub: str = Depends(require_perm("REPRINT"))):
    t = db.get(KitchenTicket, ticket_id)
    if not t:
        raise HTTPException(404, "ticket not found")
    t.reprint_count = (t.reprint_count or 0) + 1
    audit(db, sub, "kitchen_ticket", t.id, "REPRINT", reason=reason)
    db.commit()
    return {"ticket_id": t.id, "reprint_count": t.reprint_count}

@router.post("/tickets/{ticket_id}/cancel")
def cancel_ticket(ticket_id: str, reason: str = Body(..., embed=True),
                  db: Session = Depends(get_db), sub: str = Depends(require_perm("VOID"))):
    t = db.get(KitchenTicket, ticket_id)
    if not t:
        raise HTTPException(404, "ticket not found")
    t.status = KOTStatus.CANCELLED
    t.cancel_reason = reason
    audit(db, sub, "kitchen_ticket", t.id, "CANCEL", reason=reason)
    db.commit()
    return {"ticket_id": t.id, "status": t.status.value}
