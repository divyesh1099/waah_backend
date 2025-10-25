from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
import httpx
from decimal import Decimal, ROUND_HALF_UP

from app.db import get_db
from app.deps import require_auth  # phase-1: allow any logged-in cashier to print
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
)
from app.services.billing import compute_bill

router = APIRouter(prefix="/print", tags=["print"])


# --- helpers ---------------------------------------------------------------

def _money(x) -> float:
    """Round to 2dp as float for receipts."""
    if x is None:
        x = 0
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def _post_agent(url: str, payload: dict):
    """
    Fire-and-forget POST to local/edge print agent.
    We swallow errors in Phase-1.
    """
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(url, json=payload)
    except Exception:
        # printer agent might be offline, we don't crash POS flow
        pass


def _get_billing_printer(db: Session, branch_id: str | None):
    """
    Pull the RestaurantSettings for this branch, then resolve the billing printer.
    If nothing is configured, return (None, None) so caller can raise 400.
    """
    rs = (
        db.query(RestaurantSettings)
        .filter(RestaurantSettings.branch_id == branch_id)
        .first()
    )
    if not rs or not rs.billing_printer_id:
        return None, None

    p = db.get(Printer, rs.billing_printer_id)
    if not p or not p.connection_url:
        return None, None

    return rs, p


def _gather_line_items(db: Session, order_id: str) -> list[dict]:
    """
    Build receipt-ready line items:
    - item name (with variant label)
    - qty
    - unit price
    - modifiers text
    - line total
    """
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

    line_payloads: list[dict] = []
    for line, item_name, variant_label in rows:
        # build display name
        disp = item_name or ""
        if variant_label:
            disp = f"{disp} ({variant_label})"

        # gather modifiers on this line
        mods_rows = (
            db.query(
                OrderItemModifier,
                Modifier.name.label("mod_name"),
            )
            .join(Modifier, Modifier.id == OrderItemModifier.modifier_id)
            .filter(OrderItemModifier.order_item_id == line.id)
            .all()
        )
        mods_list: list[str] = []
        for om, mod_name in mods_rows:
            # e.g. "Extra Cheese +20.00"
            delta_txt = ""
            if getattr(om, "price_delta", 0):
                delta_txt = f" +{_money(om.price_delta)}"
            mods_list.append(f"{mod_name}{delta_txt}")

        qty = float(line.qty or 0)
        unit_price = _money(line.unit_price)
        line_total = _money(qty * unit_price - float(line.line_discount or 0))

        line_payloads.append(
            {
                "name": disp,
                "qty": qty,
                "unit_price": unit_price,
                "mods": mods_list,
                "line_total": line_total,
                "discount": _money(line.line_discount or 0),
                "gst_rate": float(line.gst_rate or 0),
            }
        )

    return line_payloads


def _build_print_payload(
    db: Session,
    order: Order,
    rs: RestaurantSettings,
    *,
    invoice: Invoice | None = None,
):
    """
    Shape we send to the print agent.
    Agent will format this into ESC/POS (or whatever).
    """

    # line items
    lines = _gather_line_items(db, order.id)

    # totals
    totals = compute_bill(db, order.id)  # subtotal/tax/total etc.
    paid_rows = db.query(Payment).filter(Payment.order_id == order.id).all()
    paid_sum = sum(float(p.amount or 0) for p in paid_rows)
    total_amt = float(totals.get("total", 0.0))
    due_amt = _money(total_amt - paid_sum)

    # extra order info
    table_code = None
    if getattr(order, "table_id", None):
        # quick inline fetch of the table code if available
        from app.models.core import DiningTable
        tbl = db.get(DiningTable, order.table_id)
        if tbl:
            table_code = tbl.code

    payload = {
        "restaurant": {
            "name": rs.name,
            "address": rs.address,
            "phone": rs.phone,
            "gstin": rs.gstin,
            "fssai": rs.fssai if getattr(rs, "print_fssai_on_invoice", False) else None,
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
        "footer": rs.invoice_footer,
    }

    if invoice:
        payload["invoice"] = {
            "invoice_id": invoice.id,
            "invoice_no": invoice.invoice_no,
            "invoice_dt": invoice.invoice_dt,
            "reprint_count": getattr(invoice, "reprint_count", 0),
        }

    return payload


# --- routes ----------------------------------------------------------------

@router.post("/bill/{order_id}")
async def print_bill(
    order_id: str,
    reason: str | None = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    Print a pre-invoice bill / customer check.
    This does NOT allocate invoice_no.
    """

    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, detail="order not found")

    rs, printer = _get_billing_printer(db, getattr(order, "branch_id", None))
    if not rs or not printer:
        raise HTTPException(400, detail="No billing printer configured")

    # Build payload for the print agent
    payload = _build_print_payload(db, order, rs)

    # Tell agent to print BILL
    await _post_agent(
        printer.connection_url,
        {
            "type": "BILL",
            **payload,
        },
    )

    # Audit (who printed a bill)
    db.add(
        AuditLog(
            actor_user_id=sub,
            entity="Order",
            entity_id=order_id,
            action="PRINT_BILL",
            reason=reason,
        )
    )
    db.commit()

    return {"printed": True}


@router.post("/invoice/{invoice_id}")
async def print_invoice(
    invoice_id: str,
    reason: str | None = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    Print a tax invoice (has invoice_no).
    Also bumps reprint_count and writes AuditLog.
    """

    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, detail="invoice not found")

    order = db.get(Order, inv.order_id)
    if not order:
        raise HTTPException(404, detail="linked order not found")

    rs, printer = _get_billing_printer(db, getattr(order, "branch_id", None))
    if not rs or not printer:
        raise HTTPException(400, detail="No billing printer configured")

    # Build payload including invoice block
    payload = _build_print_payload(db, order, rs, invoice=inv)

    # Send to agent
    await _post_agent(
        printer.connection_url,
        {
            "type": "INVOICE",
            **payload,
        },
    )

    # bump reprint_count and audit log
    if hasattr(inv, "reprint_count"):
        inv.reprint_count = (inv.reprint_count or 0) + 1

    db.add(
        AuditLog(
            actor_user_id=sub,
            entity="Invoice",
            entity_id=invoice_id,
            action="PRINT_INVOICE",
            reason=reason,
        )
    )

    db.commit()

    return {
        "printed": True,
        "reprint_count": getattr(inv, "reprint_count", None),
    }


@router.post("/open_drawer")
async def open_drawer(
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    Kick the cash drawer connected to the billing printer.
    """
    # naive: use first RestaurantSettings with a billing_printer_id
    rs = db.query(RestaurantSettings).filter(
        RestaurantSettings.billing_printer_id.isnot(None)
    ).first()
    if not rs:
        raise HTTPException(400, detail="No billing printer configured")

    p = db.get(Printer, rs.billing_printer_id)
    if not p or not p.cash_drawer_enabled:
        raise HTTPException(400, detail="Cash drawer not enabled for billing printer")
    if not p.connection_url:
        raise HTTPException(400, detail="Printer connection not set")

    await _post_agent(
        p.connection_url,
        {
            "type": "OPEN_DRAWER",
            "code": getattr(p, "cash_drawer_code", None),
        },
    )

    return {"opened": True}
