from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.db import get_db
from app.models.core import KitchenTicket, KitchenTicketItem, KOTStatus, AuditLog, KitchenStation, Printer
from app.deps import require_auth, require_perm
import httpx

router = APIRouter(prefix="/kot", tags=["kot"]) 


@router.post("/tickets")
def create_ticket(order_id: str, ticket_no: int, target_station: str | None = None, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    t = KitchenTicket(order_id=order_id, ticket_no=ticket_no, target_station=target_station)
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"ticket_id": t.id}


@router.post("/{ticket_id}/reprint")
async def reprint(ticket_id: str, reason: str | None = None, db: Session = Depends(get_db), sub: str = Depends(require_perm("REPRINT"))):
    t = db.get(KitchenTicket, ticket_id)
    if not t:
        raise HTTPException(404, detail="ticket not found")
    # fire to station's printer if configured
    if t.target_station:
        st = db.get(KitchenStation, t.target_station)
        pr = db.get(Printer, st.printer_id) if st and st.printer_id else None
        if pr and pr.connection_url:
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    await client.post(pr.connection_url, json={"type": "KOT", "ticket_id": t.id, "reprint": True})
            except Exception:
                pass
    if hasattr(t, "reprint_count"):
        t.reprint_count = (t.reprint_count or 0) + 1
    db.add(AuditLog(actor_user_id=sub, entity="KitchenTicket", entity_id=ticket_id, action="REPRINT", reason=reason))
    db.commit()
    return {"ok": True, "reprint_count": getattr(t, "reprint_count", None)}


@router.post("/{ticket_id}/cancel")
def cancel(ticket_id: str, reason: str | None = None, db: Session = Depends(get_db), sub: str = Depends(require_perm("VOID"))):
    t = db.get(KitchenTicket, ticket_id)
    if not t:
        raise HTTPException(404, detail="ticket not found")
    t.status = KOTStatus.CANCELLED
    if hasattr(t, "cancel_reason"):
        t.cancel_reason = reason
    db.add(AuditLog(actor_user_id=sub, entity="KitchenTicket", entity_id=ticket_id, action="CANCEL", reason=reason))
    db.commit()
    return {"ok": True}
