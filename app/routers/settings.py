# app/routers/settings.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.deps import require_auth, require_perm
from app.models.core import RestaurantSettings, Printer, PrinterType, KitchenStation

router = APIRouter(prefix="/settings", tags=["settings"])

@router.post("/restaurant")
def upsert_restaurant(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    rs = (db.query(RestaurantSettings)
            .filter(RestaurantSettings.tenant_id==body["tenant_id"],
                    RestaurantSettings.branch_id==body["branch_id"]).first())
    if not rs:
        rs = RestaurantSettings(**body)
        db.add(rs)
    else:
        for k, v in body.items():
            setattr(rs, k, v)
    db.commit(); db.refresh(rs)
    return {"id": rs.id}

@router.get("/restaurant")
def get_restaurant(tenant_id: str, branch_id: str, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    rs = (db.query(RestaurantSettings)
            .filter(RestaurantSettings.tenant_id==tenant_id,
                    RestaurantSettings.branch_id==branch_id).first())
    if not rs:
        return {}
    return {
        "id": rs.id, "name": rs.name, "logo_url": rs.logo_url, "address": rs.address, "phone": rs.phone,
        "gstin": rs.gstin, "fssai": rs.fssai, "print_fssai_on_invoice": rs.print_fssai_on_invoice,
        "gst_inclusive_default": rs.gst_inclusive_default,
        "invoice_footer": rs.invoice_footer if hasattr(rs, "invoice_footer") else None,
        "service_charge_mode": rs.service_charge_mode.name, "service_charge_value": float(rs.service_charge_value or 0),
        "packing_charge_mode": rs.packing_charge_mode.name, "packing_charge_value": float(rs.packing_charge_value or 0),
        "billing_printer_id": rs.billing_printer_id,
    }

# UPDATED: explicitly accept cash drawer fields, coerce PrinterType, and allow setting is_default
@router.post("/printers")
def add_printer(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    data = dict(body)
    if isinstance(data.get("type"), str):
        data["type"] = PrinterType[data["type"].upper()]
    # default the new fields if not provided
    if "cash_drawer_enabled" in Printer.__table__.columns:  # safe if model already updated
        data.setdefault("cash_drawer_enabled", False)
        data.setdefault("cash_drawer_code", None)
    p = Printer(**data)
    db.add(p); db.commit(); db.refresh(p)
    return {"id": p.id}

# NEW: allow editing printer settings (including cash drawer toggles)
@router.patch("/printers/{printer_id}")
def update_printer(printer_id: str, body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    p = db.get(Printer, printer_id)
    if not p:
        raise HTTPException(404, detail="printer not found")
    # only allow specific fields to be updated
    updatable = {"name","connection_url","is_default","type","cash_drawer_enabled","cash_drawer_code"}
    for k, v in body.items():
        if k in updatable:
            if k == "type" and isinstance(v, str):
                setattr(p, "type", PrinterType[v.upper()])
            else:
                setattr(p, k, v)
    db.commit(); db.refresh(p)
    return {"id": p.id}

@router.post("/stations")
def add_station(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    s = KitchenStation(**body); db.add(s); db.commit(); db.refresh(s)
    return {"id": s.id}
