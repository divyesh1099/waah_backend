"""
app/routers/settings.py — DROP-IN (2025-11-01)
• Fixes 500 on POST /settings/restaurant caused by too-long strings in *_id columns by
  resolving flexible printer references (UUID / object / URL / name) → UUID.
• Preserves existing endpoints and shapes. Backward compatible.
"""

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import AuthCtx, require_auth, require_perm
from app.models.core import (
    RestaurantSettings,
    Printer,
    PrinterType,
    KitchenStation,
    ChargeMode,
)
from app.config import settings
from app.util.media import save_image_upload

router = APIRouter(prefix="/settings", tags=["settings"])


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _assert_ctx_matches(ctx: AuthCtx, tenant_id: str | None, branch_id: str | None):
    if not tenant_id or not branch_id:
        raise HTTPException(400, detail="tenant_id and branch_id are required")
    if ctx.tenant_id and ctx.tenant_id != tenant_id:
        # hide the existence of the tenant to callers outside scope
        raise HTTPException(404, detail="not found")


def _coerce_charge_mode(v: Any, fallback: "ChargeMode") -> "ChargeMode":
    if v is None:
        return fallback
    if isinstance(v, ChargeMode):
        return v
    if isinstance(v, str):
        key = (v or "").strip().upper() or fallback.name
        if key == "FIXED":  # tolerate alias
            key = "FLAT"
        try:
            return ChargeMode[key]
        except KeyError:
            return fallback
    # for bool/number or unknown types fall back
    return fallback


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _resolve_printer_id(
    db: Session,
    ctx: AuthCtx,
    branch_id: str,
    raw: object | None,
) -> str | None:
    """
    Accept any of:
      - None
      - "<uuid>" (len == 36)
      - {"id": "<uuid>"}
      - {"connection_url": "http://..."}
      - {"name": "Billing-1"}
      - "http://..." (URL resolves via connection_url)
      - "Printer Name" (resolves via name)
    Returns UUID string or raises 400 when reference is unknown/invalid.
    """
    if raw is None:
        return None

    # dict object
    if isinstance(raw, dict):
        pid = raw.get("id") or raw.get("printer_id")
        if pid and len(str(pid).strip()) == 36:
            return str(pid).strip()

        url = raw.get("connection_url")
        if url:
            p = (
                db.query(Printer)
                .filter(
                    Printer.tenant_id == ctx.tenant_id,
                    Printer.branch_id == branch_id,
                    Printer.connection_url == str(url).strip(),
                )
                .first()
            )
            if p:
                return p.id
            raise HTTPException(400, detail=f"Unknown printer connection_url: {url}")

        nm = raw.get("name")
        if nm:
            p = (
                db.query(Printer)
                .filter(
                    Printer.tenant_id == ctx.tenant_id,
                    Printer.branch_id == branch_id,
                    Printer.name == str(nm).strip(),
                )
                .first()
            )
            if p:
                return p.id
            raise HTTPException(400, detail=f"Unknown printer name: {nm}")

        raise HTTPException(400, detail="Invalid printer reference object")

    # string
    s = str(raw).strip()
    if len(s) == 36:  # looks like UUID
        return s

    if s.lower().startswith("http"):
        p = (
            db.query(Printer)
            .filter(
                Printer.tenant_id == ctx.tenant_id,
                Printer.branch_id == branch_id,
                Printer.connection_url == s,
            )
            .first()
        )
        if p:
            return p.id
        raise HTTPException(400, detail=f"Unknown printer connection_url: {s}")

    # treat as name
    p = (
        db.query(Printer)
        .filter(
            Printer.tenant_id == ctx.tenant_id,
            Printer.branch_id == branch_id,
            Printer.name == s,
        )
        .first()
    )
    if p:
        return p.id
    raise HTTPException(400, detail=f"Unknown printer name: {s}")


# ---------------------------------------------------------------------
# Restaurant settings
# ---------------------------------------------------------------------

@router.post("/restaurant")
def upsert_restaurant(
    body: dict,  # flexible body (keeps old clients working)
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
    ctx: AuthCtx = Depends(require_auth),
):
    tenant_id = body.get("tenant_id")
    branch_id = body.get("branch_id")
    _assert_ctx_matches(ctx, tenant_id, branch_id)

    # Flexible printer reference handling
    # Accept either `billing_printer_id`, or `billing_printer` (uuid/object/url/name)
    raw_billing = body.get("billing_printer", body.get("billing_printer_id"))
    billing_printer_id = (
        _resolve_printer_id(db, ctx, branch_id, raw_billing)
        if raw_billing is not None
        else None
    )

    update_data: dict[str, Any] = {
        "tenant_id": tenant_id,
        "branch_id": branch_id,
        "name": body.get("name"),
        "logo_url": body.get("logo_url"),
        "address": body.get("address"),
        "phone": body.get("phone"),
        "gstin": body.get("gstin"),
        "fssai": body.get("fssai"),
        "print_fssai_on_invoice": bool(body.get("print_fssai_on_invoice", False)),
        "gst_inclusive_default": bool(body.get("gst_inclusive_default", False)),
        # ID after resolution only (prevents varchar(36) overflow)
        "billing_printer_id": billing_printer_id,
        "invoice_footer": body.get("invoice_footer"),
        "service_charge_mode": _coerce_charge_mode(body.get("service_charge_mode"), ChargeMode.NONE),
        "service_charge_value": _to_float(body.get("service_charge_value"), 0.0),
        "packing_charge_mode": _coerce_charge_mode(body.get("packing_charge_mode"), ChargeMode.NONE),
        "packing_charge_value": _to_float(body.get("packing_charge_value"), 0.0),
    }

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
            # Backward-compatible: only set attrs that exist on the model
            if hasattr(rs, k):
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
    ctx: AuthCtx = Depends(require_auth),
):
    _assert_ctx_matches(ctx, tenant_id, branch_id)

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
        "service_charge_mode": rs.service_charge_mode.name if getattr(rs, "service_charge_mode", None) else ChargeMode.NONE.name,
        "service_charge_value": float(getattr(rs, "service_charge_value", 0) or 0),
        "packing_charge_mode": rs.packing_charge_mode.name if getattr(rs, "packing_charge_mode", None) else ChargeMode.NONE.name,
        "packing_charge_value": float(getattr(rs, "packing_charge_value", 0) or 0),
        "billing_printer_id": getattr(rs, "billing_printer_id", None),
    }


@router.delete("/restaurant")
def delete_restaurant(
    tenant_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
    ctx: AuthCtx = Depends(require_auth),
):
    _assert_ctx_matches(ctx, tenant_id, branch_id)

    rs = (
        db.query(RestaurantSettings)
        .filter(
            RestaurantSettings.tenant_id == tenant_id,
            RestaurantSettings.branch_id == branch_id,
        )
        .first()
    )
    if rs:
        db.delete(rs)
        db.commit()

    return {"ok": True}


# ---------------------------------------------------------------------
# Printers
# ---------------------------------------------------------------------

@router.get("/printers")
def list_printers(
    tenant_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
    ctx: AuthCtx = Depends(require_auth),
):
    _assert_ctx_matches(ctx, tenant_id, branch_id)

    printers = (
        db.query(Printer)
        .filter(Printer.tenant_id == tenant_id, Printer.branch_id == branch_id)
        .all()
    )
    out: list[dict[str, Any]] = []
    for p in printers:
        out.append(
            {
                "id": p.id,
                "tenant_id": p.tenant_id,
                "branch_id": p.branch_id,
                "name": p.name,
                "type": p.type.value if hasattr(p.type, "value") else str(p.type),
                "connection_url": p.connection_url,
                "is_default": getattr(p, "is_default", False),
                "cash_drawer_enabled": getattr(p, "cash_drawer_enabled", False),
                "cash_drawer_code": getattr(p, "cash_drawer_code", None),
            }
        )
    return out


@router.post("/printers")
def add_printer(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
    ctx: AuthCtx = Depends(require_auth),
):
    data = dict(body)
    tenant_id = data.get("tenant_id")
    branch_id = data.get("branch_id")
    _assert_ctx_matches(ctx, tenant_id, branch_id)

    if isinstance(data.get("type"), str):
        data["type"] = PrinterType[data["type"].upper()]

    data.setdefault("is_default", False)
    if "cash_drawer_enabled" in Printer.__table__.columns:
        data.setdefault("cash_drawer_enabled", False)
        data.setdefault("cash_drawer_code", None)

    p = Printer(**data)
    db.add(p)
    db.commit()
    db.refresh(p)

    # CRITICAL: Unset other defaults of same type before setting this as default
    if getattr(p, "is_default", False):
        db.query(Printer).filter(
            Printer.tenant_id == p.tenant_id,
            Printer.branch_id == p.branch_id,
            Printer.type == p.type,
            Printer.id != p.id,
        ).update({"is_default": False})
    
    # Auto-link as billing printer if default or not set yet
    if p.type == PrinterType.BILLING:
        rs = (
            db.query(RestaurantSettings)
            .filter(
                RestaurantSettings.tenant_id == p.tenant_id,
                RestaurantSettings.branch_id == p.branch_id,
            )
            .first()
        )
        if rs and (getattr(p, "is_default", False) or not getattr(rs, "billing_printer_id", None)):
            if hasattr(rs, "billing_printer_id"):
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
    ctx: AuthCtx = Depends(require_auth),
):
    p = db.get(Printer, printer_id)
    if not p:
        raise HTTPException(404, detail="printer not found")

    if getattr(p, "tenant_id", None) != ctx.tenant_id or getattr(p, "branch_id", None) != ctx.branch_id:
        raise HTTPException(404, detail="not found")

    for k in {"name", "connection_url", "is_default", "type", "cash_drawer_enabled", "cash_drawer_code"}:
        if k in body:
            v = body[k]
            if k == "type" and isinstance(v, str):
                setattr(p, "type", PrinterType[v.upper()])
            else:
                setattr(p, k, v)

    # CRITICAL: Unset other defaults of same type before setting this as default
    if getattr(p, "is_default", False):
        db.query(Printer).filter(
            Printer.tenant_id == p.tenant_id,
            Printer.branch_id == p.branch_id,
            Printer.type == p.type,
            Printer.id != p.id,
        ).update({"is_default": False})
    
    db.commit()
    db.refresh(p)

    if p.type == PrinterType.BILLING and getattr(p, "is_default", False):
        rs = (
            db.query(RestaurantSettings)
            .filter(
                RestaurantSettings.tenant_id == p.tenant_id,
                RestaurantSettings.branch_id == p.branch_id,
            )
            .first()
        )
        if rs and hasattr(rs, "billing_printer_id"):
            rs.billing_printer_id = p.id
            db.commit()
            db.refresh(rs)

    return {"id": p.id}


@router.delete("/printers/{printer_id}")
def delete_printer(
    printer_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),  # Relaxed from SETTINGS_EDIT
):
    p = db.get(Printer, printer_id)
    if not p:
        raise HTTPException(404, detail="printer not found")

    if getattr(p, "tenant_id", None) != ctx.tenant_id or getattr(p, "branch_id", None) != ctx.branch_id:
        raise HTTPException(404, detail="not found")

    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/printers/{printer_id}/test")
def test_printer(
    printer_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Test a printer by sending a simple test print job.
    """
    import httpx
    import asyncio
    from datetime import datetime, timezone
    import logging
    
    logger = logging.getLogger(__name__)
    logger.info(f"🧪 Test printer called for ID: {printer_id}")
    
    p = db.get(Printer, printer_id)
    if not p:
        logger.error(f"❌ Printer not found: {printer_id}")
        raise HTTPException(404, detail="printer not found")

    if getattr(p, "tenant_id", None) != ctx.tenant_id or getattr(p, "branch_id", None) != ctx.branch_id:
        logger.error(f"❌ Permission denied for printer: {printer_id}")
        raise HTTPException(404, detail="not found")

    if not p.connection_url:
        logger.error(f"❌ Printer has no URL: {printer_id}")
        raise HTTPException(400, detail="Printer has no connection URL configured")

    logger.info(f"📡 Sending test print to: {p.connection_url}")
    
    # Build test print payload
    test_payload = {
        "type": "test",
        "printer_name": p.name,
        "printer_type": p.type.value if hasattr(p.type, 'value') else str(p.type),
        "test_time": datetime.now(timezone.utc).isoformat(),
        "message": f"✅ Test print from {p.name}",
        "lines": [
            "================================",
            f"  {p.name}",
            "================================",
            "",
            "   🧪 TEST PRINT",
            "",
            f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Type: {p.type.value if hasattr(p.type, 'value') else str(p.type)}",
            "",
            "  ✅ Connection OK",
            "",
            "================================",
        ]
    }

    # Send to printer
    try:
        async def send_print():
            async with httpx.AsyncClient(timeout=10.0) as client:
                logger.info(f"🚀 POSTing to {p.connection_url}")
                resp = await client.post(p.connection_url, json=test_payload)
                resp.raise_for_status()
                return resp.json()
        
        result = asyncio.run(send_print())
        logger.info(f"✅ Test print SUCCESS: {result}")
        
        return {
            "ok": True,
            "message": f"Test print job sent to {p.name}",
            "printer_response": result
        }
    except httpx.HTTPError as e:
        logger.error(f"❌ HTTP Error: {e}")
        raise HTTPException(502, detail=f"Printer connection failed: {str(e)}")
    except Exception as e:
        logger.error(f"❌ Exception: {type(e).__name__}: {e}")
        raise HTTPException(500, detail=f"Test print failed: {str(e)}")


# ---------------------------------------------------------------------
# Kitchen stations
# ---------------------------------------------------------------------

@router.post("/stations")
def add_station(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
    ctx: AuthCtx = Depends(require_auth),
):
    """Create a kitchen station and (optionally) link it to a printer for KOT routing."""
    tenant_id = body.get("tenant_id")
    branch_id = body.get("branch_id")
    _assert_ctx_matches(ctx, tenant_id, branch_id)

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
    ctx: AuthCtx = Depends(require_auth),
):
    _assert_ctx_matches(ctx, tenant_id, branch_id)

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


# ---------------------------------------------------------------------
# Logo upload
# ---------------------------------------------------------------------

@router.post("/restaurant/logo")
async def upload_restaurant_logo(
    tenant_id: str = Form(...),
    branch_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
    ctx: AuthCtx = Depends(require_auth),
):
    from app.config import settings as cfg

    _assert_ctx_matches(ctx, tenant_id, branch_id)

    allowed_types = {"image/png", "image/jpeg", "image/jpg"}
    if file.content_type not in allowed_types:
        raise HTTPException(400, detail="Only PNG or JPEG allowed")

    public_url = await save_image_upload(
        file,
        subdir=f"logos/{tenant_id}/{branch_id}",
        allowed_types=allowed_types,
    )

    rs = (
        db.query(RestaurantSettings)
        .filter(
            RestaurantSettings.tenant_id == tenant_id,
            RestaurantSettings.branch_id == branch_id,
        )
        .first()
    )
    if not rs:
        raise HTTPException(400, detail="Restaurant settings not found. Save /settings/restaurant first, then upload logo.")

    if hasattr(rs, "logo_url"):
        rs.logo_url = public_url
        db.commit()
        db.refresh(rs)

    return {"logo_url": public_url}
