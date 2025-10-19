from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import httpx
from datetime import datetime, timezone

from app.db import get_db
from app.deps import require_auth, require_perm
from app.models.core import Invoice, RestaurantSettings, Printer, AuditLog

router = APIRouter(prefix="/print", tags=["print"]) 


async def _post_agent(url: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(url, json=payload)
    except Exception:
        # swallow for Phase-1; agent may be offline
        pass


@router.post("/invoice/{invoice_id}")
async def print_invoice(invoice_id: str, reason: str | None = None, db: Session = Depends(get_db), sub: str = Depends(require_perm("REPRINT"))):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, detail="invoice not found")
    rs = db.query(RestaurantSettings).first()
    if not rs or not rs.billing_printer_id:
        raise HTTPException(400, detail="No billing printer configured")
    p = db.get(Printer, rs.billing_printer_id)
    if not p or not p.connection_url:
        raise HTTPException(400, detail="Printer connection not set")

    payload = {"type": "INVOICE", "invoice_id": inv.id, "invoice_no": inv.invoice_no}
    await _post_agent(p.connection_url, payload)

    # bump reprint count & audit
    if hasattr(inv, "reprint_count"):
        inv.reprint_count = (inv.reprint_count or 0) + 1
    db.add(
        AuditLog(
            actor_user_id=sub,
            entity="Invoice",
            entity_id=invoice_id,
            action="REPRINT",
            reason=reason,
        )
    )
    db.commit()
    return {"printed": True, "reprint_count": getattr(inv, "reprint_count", None)}


@router.post("/open_drawer")
async def open_drawer(db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    rs = db.query(RestaurantSettings).first()
    if not rs or not rs.billing_printer_id:
        raise HTTPException(400, detail="No billing printer configured")
    p = db.get(Printer, rs.billing_printer_id)
    if not p or not p.cash_drawer_enabled:
        raise HTTPException(400, detail="Cash drawer not enabled for billing printer")
    if not p.connection_url:
        raise HTTPException(400, detail="Printer connection not set")
    await _post_agent(p.connection_url, {"type": "OPEN_DRAWER", "code": getattr(p, "cash_drawer_code", None)})
    return {"opened": True}

