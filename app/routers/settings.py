from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.deps import require_auth, require_perm
from app.models.core import RestaurantSettings, Printer, PrinterType, KitchenStation

router = APIRouter(prefix="/settings", tags=["settings"])

@router.post("/restaurant")
def upsert_restaurant(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    # expects: tenant_id, branch_id, name, logo_url, address, phone, gstin, fssai,
    #          print_fssai_on_invoice, gst_inclusive_default,
    #          service_charge_mode, service_charge_value,
    #          packing_charge_mode, packing_charge_value, billing_printer_id?, invoice_footer?
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
        "invoice_footer": rs.invoice_footer,
        "service_charge_mode": rs.service_charge_mode.name, "service_charge_value": float(rs.service_charge_value or 0),
        "packing_charge_mode": rs.packing_charge_mode.name, "packing_charge_value": float(rs.packing_charge_value or 0),
        "billing_printer_id": rs.billing_printer_id,
    }

@router.post("/printers")
def add_printer(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    # body: {tenant_id, branch_id, name, type: BILLING|KITCHEN, connection_url?, is_default?}
    p = Printer(**body); db.add(p); db.commit(); db.refresh(p)
    return {"id": p.id}

@router.post("/stations")
def add_station(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    # body: {tenant_id, branch_id, name, printer_id?}
    s = KitchenStation(**body); db.add(s); db.commit(); db.refresh(s)
    return {"id": s.id}
