# app/routers/print.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import httpx
from decimal import Decimal, ROUND_HALF_UP
from app.models.core import PrinterType
from app.db import get_db
from app.deps import AuthCtx, require_auth
from app.models.core import (
    AuditLog,
    Order,
    OrderItem,
    OrderItemModifier,
    Modifier,
    MenuItem,
    ItemVariant,
    Payment,
    Invoice,
    RestaurantSettings,
    Printer,
    DiningTable,
    Branch,
)
from app.services.billing import compute_bill

router = APIRouter(prefix="/print", tags=["print"])

# ----------------- helpers -----------------

def _money(x) -> float:
    if x is None:
        x = 0
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

async def _post_agent(url: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(url, json=payload)
    except Exception:
        # printer agent could be offline; printing shouldn't crash POS flow
        pass

def _ensure_order_access(db: Session, order_id: str, ctx: AuthCtx) -> Order:
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, detail="order not found")
    if hasattr(o, "tenant_id") and o.tenant_id != ctx.tenant_id:
        raise HTTPException(404, detail="order not found")
    if ctx.branch_id and hasattr(o, "branch_id") and o.branch_id != ctx.branch_id:
        raise HTTPException(404, detail="order not found")
    return o

def _gather_line_items(db: Session, order_id: str) -> list[dict]:
    rows = (
        db.query(
            OrderItem,
            MenuItem.name.label("item_name"),
            ItemVariant.label.label("variant_label"),
        )
        .join(MenuItem, MenuItem.id == OrderItem.item_id)
        .outerjoin(ItemVariant, ItemVariant.id == OrderItem.variant_id)
        .filter(OrderItem.order_id == order_id)
        .all()
    )
    out: list[dict] = []
    for line, item_name, variant_label in rows:
        disp = item_name or ""
        if variant_label:
            disp = f"{disp} ({variant_label})"

        mods_rows = (
            db.query(OrderItemModifier, Modifier.name.label("mod_name"))
            .join(Modifier, Modifier.id == OrderItemModifier.modifier_id)
            .filter(OrderItemModifier.order_item_id == line.id)
            .all()
        )
        mods: list[str] = []
        for om, mod_name in mods_rows:
            delta = _money(getattr(om, "price_delta", 0))
            mods.append(f"{mod_name}{f' +{delta}' if delta else ''}")

        qty = float(line.qty or 0)
        unit = _money(line.unit_price)
        line_total = _money(qty * unit - float(line.line_discount or 0))

        out.append(
            {
                "name": disp,
                "qty": qty,
                "unit_price": unit,
                "mods": mods,
                "line_total": line_total,
                "discount": _money(line.line_discount or 0),
                "gst_rate": float(line.gst_rate or 0),
            }
        )
    return out

def _build_print_payload(db: Session, order: Order, rs: RestaurantSettings | None, *, invoice: Invoice | None = None):
    lines = _gather_line_items(db, order.id)
    totals = compute_bill(db, order.id)
    paid_rows = db.query(Payment).filter(Payment.order_id == order.id).all()
    paid_sum = sum(float(p.amount or 0) for p in paid_rows)
    due_amt = _money(float(totals.get("total", 0.0)) - paid_sum)

    table_code = None
    if getattr(order, "table_id", None):
        tbl = db.get(DiningTable, order.table_id)
        if tbl:
            table_code = getattr(tbl, "code", None) or getattr(tbl, "name", None)

    payload = {
        "restaurant": {
            "name": getattr(rs, "name", None) if rs else None,
            "address": getattr(rs, "address", None) if rs else None,
            "phone": getattr(rs, "phone", None) if rs else None,
            "gstin": getattr(rs, "gstin", None) if rs else None,
            "fssai": (rs.fssai if (rs and getattr(rs, "print_fssai_on_invoice", False)) else None),
        },
        "order": {
            "id": order.id,
            "order_no": order.order_no,
            "channel": getattr(order.channel, "value", str(order.channel)),
            "table_code": table_code,
            "pax": getattr(order, "pax", None),
            "opened_at": getattr(order, "opened_at", None),
            "closed_at": getattr(order, "closed_at", None),
        },
        "lines": lines,
        "totals": {
            "subtotal": _money(totals.get("subtotal", 0)),
            "tax": _money(totals.get("tax", 0)),
            "grand_total": _money(totals.get("total", 0)),
            "paid": _money(paid_sum),
            "due": due_amt,
        },
        "footer": getattr(rs, "invoice_footer", None) if rs else None,
    }
    if invoice:
        payload["invoice"] = {
            "invoice_id": invoice.id,
            "invoice_no": invoice.invoice_no,
            "invoice_dt": invoice.invoice_dt,
            "reprint_count": getattr(invoice, "reprint_count", 0),
            "cashier_user_id": getattr(invoice, "cashier_user_id", None),
        }
    return payload

def _get_billing_printer(db: Session, tenant_id: str | None, branch_id: str | None):
    """
    Resolve the active BILLING/BILL printer for a branch with fallbacks:
      1) RestaurantSettings.billing_printer_id
      2) Branch default BILL printer (is_default=True)
      3) Any BILL printer for the branch
    Returns (rs, printer) or (None, None).
    """

    def _has_url(p: Printer | None) -> bool:
        return bool(p and getattr(p, "connection_url", None) and p.connection_url.strip())

    # Accept both enum and string representations
    bill_tokens = []
    # enum tokens (if column type is Enum)
    if hasattr(PrinterType, "BILLING"):
        bill_tokens.append(PrinterType.BILLING)
    if hasattr(PrinterType, "BILL"):
        bill_tokens.append(PrinterType.BILL)
    # string tokens (if column type is String)
    bill_tokens.extend(["BILLING", "BILL"])

    if not branch_id:
        return None, None

    # Restaurant settings first
    rs = (
        db.query(RestaurantSettings)
        .filter(RestaurantSettings.branch_id == branch_id)
        .filter(RestaurantSettings.tenant_id == tenant_id) if tenant_id
        else db.query(RestaurantSettings).filter(RestaurantSettings.branch_id == branch_id)
    ).first()

    if rs and rs.billing_printer_id:
        p = db.get(Printer, rs.billing_printer_id)
        if _has_url(p):
            return rs, p

    # Default BILL printer (is_default=True)
    q = db.query(Printer).filter(Printer.branch_id == branch_id)
    if tenant_id:
        q = q.filter(Printer.tenant_id == tenant_id)
    p = q.filter(Printer.type.in_(bill_tokens)).filter(Printer.is_default == True).first()  # noqa: E712
    if _has_url(p):
        return rs, p

    # Any BILL printer
    q = db.query(Printer).filter(Printer.branch_id == branch_id)
    if tenant_id:
        q = q.filter(Printer.tenant_id == tenant_id)
    p = q.filter(Printer.type.in_(bill_tokens)).first()
    if _has_url(p):
        return rs, p

    return None, None

# ----------------- routes -----------------

@router.post("/bill/{order_id}")
async def print_bill(
    order_id: str,
    reason: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    order = _ensure_order_access(db, order_id, ctx)
    rs, printer = _get_billing_printer(db, getattr(order, "tenant_id", None), getattr(order, "branch_id", None))
    if not printer:
        raise HTTPException(400, detail="No billing printer configured")

    payload = _build_print_payload(db, order, rs)
    await _post_agent(printer.connection_url, {"type": "BILL", **payload})

    db.add(AuditLog(actor_user_id=ctx.user_id, entity="Order", entity_id=order_id, action="PRINT_BILL", reason=reason))
    db.commit()
    return {"printed": True}

@router.post("/invoice/{invoice_id}")
async def print_invoice(
    invoice_id: str,
    reason: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, detail="invoice not found")
    order = _ensure_order_access(db, inv.order_id, ctx)

    rs, printer = _get_billing_printer(db, getattr(order, "tenant_id", None), getattr(order, "branch_id", None))
    if not rs or not printer:
        raise HTTPException(400, detail="No billing printer configured")

    payload = _build_print_payload(db, order, rs, invoice=inv)
    await _post_agent(printer.connection_url, {"type": "INVOICE", **payload})

    if hasattr(inv, "reprint_count"):
        inv.reprint_count = (inv.reprint_count or 0) + 1

    db.add(AuditLog(actor_user_id=ctx.user_id, entity="Invoice", entity_id=invoice_id, action="PRINT_INVOICE", reason=reason))
    db.commit()
    return {"printed": True, "reprint_count": getattr(inv, "reprint_count", None)}

@router.post("/open_drawer")
async def open_drawer(
    tenant_id: str | None = None,
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    eff_tenant = tenant_id or ctx.tenant_id
    eff_branch = branch_id or ctx.branch_id

    rs = None
    printer = None

    if eff_branch:
        br = db.get(Branch, eff_branch)
        if (not br) or (hasattr(br, "tenant_id") and br.tenant_id != eff_tenant):
            raise HTTPException(404, detail="branch not found")
        rs, printer = _get_billing_printer(db, eff_tenant, eff_branch)

    if not rs or not printer:
        rs = db.query(RestaurantSettings).filter(RestaurantSettings.billing_printer_id.isnot(None)).first()
        if rs:
            printer = db.get(Printer, rs.billing_printer_id)

    if not rs or not printer:
        raise HTTPException(400, detail="No billing printer configured")
    if not getattr(printer, "cash_drawer_enabled", False):
        raise HTTPException(400, detail="Cash drawer not enabled for billing printer")
    if not printer.connection_url:
        raise HTTPException(400, detail="Printer connection not set")

    await _post_agent(printer.connection_url, {"type": "OPEN_DRAWER", "code": getattr(printer, "cash_drawer_code", None)})
    return {"opened": True}
