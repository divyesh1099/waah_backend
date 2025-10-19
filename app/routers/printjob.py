from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
import httpx
from app.db import get_db
from app.deps import require_perm
from app.models.core import Invoice, RestaurantSettings, Printer
from app.util.audit import audit

router = APIRouter(prefix="/print", tags=["print"])

async def _post_agent(url: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(url, json=payload)
    except Exception:
        pass

@router.post("/invoice/{invoice_id}")
async def print_invoice(invoice_id: str, reason: str = Body(..., embed=True),
                        db: Session = Depends(get_db), sub: str = Depends(require_perm("REPRINT"))):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, detail="invoice not found")
    rs = db.query(RestaurantSettings).first()
    if not rs or not rs.billing_printer_id:
        raise HTTPException(400, detail="No billing printer configured")
    p = db.get(Printer, rs.billing_printer_id)
    if not p or not p.connection_url:
        raise HTTPException(400, detail="Printer connection not set")
    await _post_agent(p.connection_url, {"type":"INVOICE","invoice_id":inv.id,"invoice_no":inv.invoice_no})
    inv.reprint_count = (inv.reprint_count or 0) + 1
    audit(db, sub, "invoice", inv.id, "REPRINT", reason=reason)
    db.commit()
    return {"printed": True, "reprint_count": inv.reprint_count}

@router.post("/open_cash_drawer")
async def open_cash_drawer(db: Session = Depends(get_db), sub: str = Depends(require_perm("SHIFT_CLOSE"))):
    rs = db.query(RestaurantSettings).first()
    if not rs or not rs.billing_printer_id:
        raise HTTPException(400, detail="No billing printer configured")
    p = db.get(Printer, rs.billing_printer_id)
    if not p or not p.connection_url:
        raise HTTPException(400, detail="Printer connection not set")
    await _post_agent(p.connection_url, {"type":"OPEN_DRAWER"})
    audit(db, sub, "printer", p.id, "OPEN_DRAWER")
    db.commit()
    return {"ok": True}
