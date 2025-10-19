from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone, date
from decimal import Decimal, ROUND_HALF_UP

from app.db import get_db
from app.deps import require_auth
from app.schemas.orders import OrderIn, OrderOut, OrderItemIn, PaymentIn
from app.models.core import (
    Order, OrderStatus, OrderItem, Payment, MenuItem, ItemVariant,
    KitchenTicket, KitchenTicketItem, RecipeBOM, StockMove, StockMoveType,
    RestaurantSettings, Branch, Customer
)
from app.services.billing import compute_bill

router = APIRouter(prefix="/orders", tags=["orders"]) 


def _money(x: float | Decimal) -> float:
    return float(Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _split_tax(
    *,
    branch_state: str | None,
    customer_state: str | None,
    amount: float,
) -> dict:
    """Return dict with cgst, sgst, igst split for amount."""
    amount = float(amount or 0)
    if branch_state and customer_state and branch_state != customer_state:
        return {"cgst": 0.0, "sgst": 0.0, "igst": _money(amount)}
    # intra-state (default)
    half = _money(amount / 2)
    return {"cgst": half, "sgst": _money(amount - half), "igst": 0.0}

def _q3(x) -> Decimal:
    # use string to avoid float binary artifacts
    return Decimal(str(x)).quantize(Decimal("0.001"))


@router.post("/", response_model=OrderOut)
def open_order(body: OrderIn, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    o = Order(
        **body.model_dump(),
        status=OrderStatus.OPEN,
        opened_by_user_id=sub,
        opened_at=datetime.now(timezone.utc),
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return OrderOut(id=o.id, status=o.status.value, **body.model_dump())


@router.post("/{order_id}/items")
def add_item(order_id: str, body: OrderItemIn, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    if order_id != body.order_id:
        raise HTTPException(400, detail="order_id mismatch")

    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, detail="order not found")

    mitem: MenuItem | None = db.get(MenuItem, body.item_id)
    if not mitem:
        raise HTTPException(404, detail="menu item not found")

    # default price can be taken from variant if provided (base_price), but API supplies unit_price already.
    line = OrderItem(**body.model_dump(), gst_rate=mitem.gst_rate)

    # derive tax split (inclusive/exclusive per item setting)
    base = float(body.qty) * float(body.unit_price) - float(line.line_discount or 0)
    if float(mitem.gst_rate or 0) > 0:
        if bool(mitem.tax_inclusive):
            taxable = base / (1 + float(mitem.gst_rate) / 100)
            tax_total = base - taxable
        else:
            taxable = base
            tax_total = taxable * float(mitem.gst_rate) / 100
    else:
        taxable = base
        tax_total = 0.0

    # intra/inter-state from Branch vs Customer
    branch = db.get(Branch, order.branch_id) if order.branch_id else None
    cust = db.get(Customer, order.customer_id) if order.customer_id else None
    split = _split_tax(
        branch_state=(branch.state_code if branch and hasattr(branch, "state_code") else None),
        customer_state=(cust.state_code if cust and hasattr(cust, "state_code") else None),
        amount=tax_total,
    )
    line.taxable_value = _money(taxable)
    line.cgst = _money(split["cgst"])\
        if hasattr(line, "cgst") else _money(split["cgst"])  # keep even if older schema
    line.sgst = _money(split["sgst"]) if hasattr(line, "sgst") else _money(split["sgst"]) 
    line.igst = _money(split["igst"]) if hasattr(line, "igst") else _money(split["igst"]) 

    db.add(line)
    db.flush()

    # inventory deduction (BOM)
    recipes = db.query(RecipeBOM).filter(RecipeBOM.item_id == mitem.id).all()
    for r in recipes:
        qty_delta = (_q3(r.qty) * _q3(body.qty)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        db.add(
            StockMove(
                ingredient_id=r.ingredient_id,
                type=StockMoveType.SALE,
                qty_change=-qty_delta,  # Decimal with 3dp
                reason=f"Order {order_id}",
                ref_order_id=order_id,
            )
        )

    # auto-KOT per station
    if mitem.kitchen_station_id:
        station_ticket = (
            db.query(KitchenTicket)
            .filter(
                KitchenTicket.order_id == order_id,
                KitchenTicket.target_station == mitem.kitchen_station_id,
            )
            .first()
        )
        if not station_ticket:
            station_ticket = KitchenTicket(
                order_id=order_id,
                ticket_no=int(datetime.now().timestamp()),
                target_station=mitem.kitchen_station_id,
            )
            db.add(station_ticket)
            db.flush()
        db.add(
            KitchenTicketItem(
                ticket_id=station_ticket.id, order_item_id=line.id, qty=body.qty
            )
        )

    db.commit()
    return {"id": line.id}


@router.post("/{order_id}/pay")
def pay(order_id: str, body: PaymentIn, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    if order_id != body.order_id:
        raise HTTPException(400, detail="order_id mismatch")

    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, detail="order not found")

    p = Payment(**body.model_dump(), paid_at=datetime.now(timezone.utc))
    db.add(p)
    db.flush()

    # compute totals and close if fully paid (Phase-1: assume single payment closes order)
    totals = compute_bill(db, order_id)
    o.status = OrderStatus.CLOSED
    o.closed_at = datetime.now(timezone.utc)
    # mark who closed (cashier)
    if hasattr(o, "closed_by_user_id"):
        o.closed_by_user_id = sub

    db.commit()
    return {
        "payment_id": p.id,
        "order_status": o.status.value,
        "totals": totals,
    }


@router.post("/{order_id}/invoice")
def create_invoice(order_id: str, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    from app.models.core import Invoice

    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, detail="order not found")
    inv_no = f"INV-{int(datetime.now().timestamp())}"
    inv = Invoice(order_id=order_id, invoice_no=inv_no, invoice_dt=datetime.now(timezone.utc))
    # store cashier on invoice if schema supports it
    if hasattr(inv, "cashier_user_id"):
        inv.cashier_user_id = sub
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return {"invoice_id": inv.id, "invoice_no": inv.invoice_no}
