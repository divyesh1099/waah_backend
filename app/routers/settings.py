# app/routers/settings.py
import os
import shutil
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_auth, require_perm
from app.models.core import (
    RestaurantSettings,
    Printer,
    PrinterType,
    KitchenStation,
    ChargeMode,
)
from app.config import settings

router = APIRouter(prefix="/settings", tags=["settings"])


# ------------------------
# RESTAURANT SETTINGS
# ------------------------

@router.post("/restaurant")
def upsert_restaurant(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Creates or updates RestaurantSettings (one row per tenant+branch).
    This covers:
      - restaurant name/address/phone/logo
      - GSTIN/FSSAI
      - service_charge / packing_charge
      - gst_inclusive_default
      - billing_printer_id (optional)
      - invoice_footer
    """

    tenant_id = body.get("tenant_id")
    branch_id = body.get("branch_id")

    if not tenant_id or not branch_id:
        raise HTTPException(400, detail="tenant_id and branch_id are required")

    def _coerce_charge_mode(v, fallback: "ChargeMode"):
        """
        Accepts:
        - ChargeMode enum instance
        - strings like "NONE", "FLAT", "FIXED", "PERCENT" (any case)
        - None / '' -> fallback
        Maps legacy "FIXED" -> "FLAT".
        """
        if v is None:
            return fallback

        # already enum?
        if isinstance(v, ChargeMode):
            return v

        # weird junk like bool/number -> fallback
        if isinstance(v, (bool, int, float)):
            return fallback

        if isinstance(v, str):
            key = v.strip().upper()
            if not key:
                return fallback

            # backward compat: old frontend used "FIXED", backend enum is "FLAT"
            if key == "FIXED":
                key = "FLAT"

            try:
                return ChargeMode[key]
            except KeyError:
                return fallback

        # anything else -> fallback
        return fallback

    # numeric helpers
    def _to_float(v, default=0.0):
        try:
            if v is None or v == "":
                return default
            return float(v)
        except Exception:
            return default

    service_mode = _coerce_charge_mode(
        body.get("service_charge_mode"),
        ChargeMode.NONE,
    )
    packing_mode = _coerce_charge_mode(
        body.get("packing_charge_mode"),
        ChargeMode.NONE,
    )

    service_val = _to_float(body.get("service_charge_value"), 0.0)
    packing_val = _to_float(body.get("packing_charge_value"), 0.0)

    # sanitize booleans with sane defaults
    print_fssai = bool(body.get("print_fssai_on_invoice", False))
    gst_inclusive = bool(body.get("gst_inclusive_default", False))

    update_data = {
        "tenant_id": tenant_id,
        "branch_id": branch_id,
        "name": body.get("name"),
        "logo_url": body.get("logo_url"),
        "address": body.get("address"),
        "phone": body.get("phone"),
        "gstin": body.get("gstin"),
        "fssai": body.get("fssai"),
        "print_fssai_on_invoice": print_fssai,
        "gst_inclusive_default": gst_inclusive,
        "billing_printer_id": body.get("billing_printer_id"),
        "invoice_footer": body.get("invoice_footer"),
        "service_charge_mode": service_mode,
        "service_charge_value": service_val,
        "packing_charge_mode": packing_mode,
        "packing_charge_value": packing_val,
    }

    # upsert
    rs = (
        db.query(RestaurantSettings)
        .filter(
            RestaurantSettings.tenant_id == tenant_id,
            RestaurantSettings.branch_id == branch_id,
        )
        .first()
    )
    if not rs:
        rs = RestaurantSettings(**update_data)
        db.add(rs)
    else:
        for k, v in update_data.items():
            setattr(rs, k, v)

    db.commit()
    db.refresh(rs)
    return {"id": rs.id}


@router.get("/restaurant")
def get_restaurant(
    tenant_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    Fetch RestaurantSettings for a tenant+branch so the frontend
    can display / edit settings and know the billing_printer_id.
    """
    rs = (
        db.query(RestaurantSettings)
        .filter(
            RestaurantSettings.tenant_id == tenant_id,
            RestaurantSettings.branch_id == branch_id,
        )
        .first()
    )
    if not rs:
        return {}

    return {
        "id": rs.id,
        "tenant_id": rs.tenant_id,
        "branch_id": rs.branch_id,
        "name": rs.name,
        "logo_url": rs.logo_url,
        "address": rs.address,
        "phone": rs.phone,
        "gstin": rs.gstin,
        "fssai": rs.fssai,
        "print_fssai_on_invoice": rs.print_fssai_on_invoice,
        "gst_inclusive_default": rs.gst_inclusive_default,
        "invoice_footer": getattr(rs, "invoice_footer", None),
        "service_charge_mode": rs.service_charge_mode.name,
        "service_charge_value": float(rs.service_charge_value or 0),
        "packing_charge_mode": rs.packing_charge_mode.name,
        "packing_charge_value": float(rs.packing_charge_value or 0),
        "billing_printer_id": rs.billing_printer_id,
    }


# ------------------------
# PRINTERS
# ------------------------

@router.get("/printers")
def list_printers(
    tenant_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    List all printers for that branch, so UI (or you via Postman)
    can pick which one should bill.
    """
    printers = (
        db.query(Printer)
        .filter(
            Printer.tenant_id == tenant_id,
            Printer.branch_id == branch_id,
        )
        .all()
    )

    out = []
    for p in printers:
        out.append(
            {
                "id": p.id,
                "tenant_id": p.tenant_id,
                "branch_id": p.branch_id,
                "name": p.name,
                "type": p.type.value
                if hasattr(p.type, "value")
                else str(p.type),
                "connection_url": p.connection_url,
                "is_default": p.is_default,
                "cash_drawer_enabled": getattr(
                    p, "cash_drawer_enabled", False
                ),
                "cash_drawer_code": getattr(
                    p, "cash_drawer_code", None
                ),
            }
        )
    return out


@router.post("/printers")
def add_printer(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Create a printer.
    body should include:
      tenant_id, branch_id,
      name,
      type: "BILLING" | "KITCHEN",
      connection_url (like http://192.168.x.x:9100/print or agent webhook),
      is_default (bool),
      cash_drawer_enabled (bool),
      cash_drawer_code (str)
    """
    data = dict(body)

    # coerce type string -> PrinterType enum
    if isinstance(data.get("type"), str):
        data["type"] = PrinterType[data["type"].upper()]

    # sane defaults
    data.setdefault("is_default", False)
    if "cash_drawer_enabled" in Printer.__table__.columns:
        data.setdefault("cash_drawer_enabled", False)
        data.setdefault("cash_drawer_code", None)

    p = Printer(**data)
    db.add(p)
    db.commit()
    db.refresh(p)

    # If this is a BILLING printer, auto-wire it into RestaurantSettings
    # so printing / cash drawer works without manual SQL.
    if p.type == PrinterType.BILLING:
        rs = (
            db.query(RestaurantSettings)
            .filter(
                RestaurantSettings.tenant_id == p.tenant_id,
                RestaurantSettings.branch_id == p.branch_id,
            )
            .first()
        )
        if rs:
            # Priority:
            #   - If caller marked it is_default = True,
            #     OR there's no billing_printer_id yet.
            if p.is_default or not rs.billing_printer_id:
                rs.billing_printer_id = p.id
                db.commit()
                db.refresh(rs)

    return {"id": p.id}


@router.patch("/printers/{printer_id}")
def update_printer(
    printer_id: str,
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Update printer info. You can also flip is_default=true here
    to make this the active billing printer for that branch.
    """
    p = db.get(Printer, printer_id)
    if not p:
        raise HTTPException(404, detail="printer not found")

    updatable = {
        "name",
        "connection_url",
        "is_default",
        "type",
        "cash_drawer_enabled",
        "cash_drawer_code",
    }

    for k, v in body.items():
        if k not in updatable:
            continue
        if k == "type" and isinstance(v, str):
            setattr(p, "type", PrinterType[v.upper()])
        else:
            setattr(p, k, v)

    db.commit()
    db.refresh(p)

    # If it's a BILLING printer and is_default = True,
    # make sure RestaurantSettings.billing_printer_id matches this printer.
    if p.type == PrinterType.BILLING and getattr(p, "is_default", False):
        rs = (
            db.query(RestaurantSettings)
            .filter(
                RestaurantSettings.tenant_id == p.tenant_id,
                RestaurantSettings.branch_id == p.branch_id,
            )
            .first()
        )
        if rs:
            rs.billing_printer_id = p.id
            db.commit()
            db.refresh(rs)

    return {"id": p.id}


# ------------------------
# KITCHEN STATIONS
# ------------------------

@router.post("/stations")
def add_station(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Create a kitchen station and (optionally) link it to a printer
    so KOTs route correctly.
    """
    s = KitchenStation(**body)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id}


@router.get("/stations")
def list_stations(
    tenant_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    stations = (
        db.query(KitchenStation)
        .filter(
            KitchenStation.tenant_id == tenant_id,
            KitchenStation.branch_id == branch_id,
        )
        .all()
    )
    return [
        {
            "id": st.id,
            "tenant_id": st.tenant_id,
            "branch_id": st.branch_id,
            "name": st.name,
            "printer_id": st.printer_id,
        }
        for st in stations
    ]

@router.post("/restaurant/logo")
def upload_restaurant_logo(
    tenant_id: str = Form(...),
    branch_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Upload a logo image for this tenant+branch.
    Saves file under MEDIA_ROOT and updates RestaurantSettings.logo_url.
    Returns { "logo_url": "/media/xxx.png" }

    Frontend flow:
    1. call this with multipart/form-data
    2. take returned logo_url
    3. include that logo_url in /settings/restaurant POST body
    """

    # basic content-type guard
    allowed_types = {"image/png", "image/jpeg", "image/jpg"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Only PNG or JPEG allowed",
        )

    # build a safe unique filename
    orig_ext = os.path.splitext(file.filename or "")[1].lower()
    if orig_ext not in [".png", ".jpg", ".jpeg"]:
        # fall back based on mime
        orig_ext = ".png" if file.content_type == "image/png" else ".jpg"

    unique_name = (
        f"logo_{tenant_id}_{branch_id}_{uuid.uuid4().hex}{orig_ext}"
    )

    abs_path = os.path.join(settings.MEDIA_ROOT, unique_name)
    # actually write the uploaded bytes
    with open(abs_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    public_url = f"{settings.MEDIA_URL_BASE}/{unique_name}"

    # fetch that branch's settings row
    rs = (
        db.query(RestaurantSettings)
        .filter(
            RestaurantSettings.tenant_id == tenant_id,
            RestaurantSettings.branch_id == branch_id,
        )
        .first()
    )

    if not rs:
        # We don't assume defaults for name/gstin/etc here,
        # because RestaurantSettings.name is probably NOT NULL.
        # Force caller to upsert /settings/restaurant first.
        raise HTTPException(
            status_code=400,
            detail="Restaurant settings not found. "
                   "Save /settings/restaurant first, then upload logo.",
        )

    rs.logo_url = public_url
    db.commit()
    db.refresh(rs)

    return {"logo_url": public_url}
