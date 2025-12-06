from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from datetime import datetime, timezone, date
from decimal import Decimal, ROUND_HALF_UP

from app.db import get_db
from app.deps import AuthCtx, require_auth
from app.schemas.orders import OrderIn, OrderOut, OrderItemIn, PaymentIn, OrderStatusUpdate
from app.models.core import (
    AuditLog, Invoice, Order, OrderStatus, OrderItem, Payment, MenuItem, ItemVariant,
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

# -------- NEW: multi-tenant/branch helpers --------

def _effective_branch_id(db: Session, ctx: AuthCtx, provided_branch_id: str | None) -> str | None:
    """
    If token has ctx.branch_id -> use it.
    Else, accept provided_branch_id but verify that it belongs to ctx.tenant_id.
    If neither is present, return None (some channels may be out-of-branch).
    """
    bid = (ctx.branch_id or (provided_branch_id or "").strip()) or None
    if not bid:
        return None
    br = db.get(Branch, bid)
    if (not br) or br.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="branch not found")
    return bid

def _ensure_order_access(db: Session, order_id: str, ctx: AuthCtx) -> Order:
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(status_code=404, detail="order not found")
    # tenant must match if column exists
    if hasattr(o, "tenant_id") and o.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="order not found")
    # if ctx is scoped to a branch, order must be in that branch too
    if ctx.branch_id and hasattr(o, "branch_id") and o.branch_id != ctx.branch_id:
        raise HTTPException(status_code=404, detail="order not found")
    return o

# -----------------------------------------------

@router.get("/")
def list_orders(
    status: str | None = None,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
    page: int = 1,
    branch_id: Optional[str] = None,
    size: int = 20,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    List orders (paged) for the logged-in user’s tenant/branch context.

    Query params:
      - status: "OPEN", "CLOSED", etc. (optional)
      - start_dt: ISO 8601 timestamp (optional)
      - end_dt: ISO 8601 timestamp (optional)
      - page:   1-based page index
      - size:   page size

    Response shape matches what the Flutter app expects in ApiClient.listPage():
      { "items": [ { ...order fields... } ], "total": <int> }
    """

    q = db.query(Order)

    # tenant/branch scoping
    if hasattr(Order, "tenant_id"):
        q = q.filter(Order.tenant_id == ctx.tenant_id)
    eff_branch = ctx.branch_id or (branch_id or "").strip()
    if eff_branch and hasattr(Order, "branch_id"):
        q = q.filter(Order.branch_id == eff_branch)

    # optional filter by status
    if status:
        try:
            wanted = OrderStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid status")
        q = q.filter(Order.status == wanted)

    # NEW: optional filter by datetime range (on opened_at)
    if start_dt:
        q = q.filter(Order.opened_at >= start_dt)
    if end_dt:
        q = q.filter(Order.opened_at <= end_dt)

    # simple pagination math
    if page < 1:
        page = 1
    if size < 1:
        size = 20
    offset = (page - 1) * size

    total = q.count()

    rows = (
        q.order_by(
            Order.opened_at.desc(),
            Order.order_no.desc(),
            Order.id.desc(),
        )
        .offset(offset)
        .limit(size)
        .all()
    )

    items: list[dict] = []
    for o in rows:
        items.append({
            "id": o.id,
            "tenant_id": getattr(o, "tenant_id", None),
            "branch_id": getattr(o, "branch_id", None),
            "order_no": getattr(o, "order_no", None),

            # enums come back in our Flutter model as strings like "DINE_IN" / "OPEN"
            "channel": getattr(o.channel, "value", o.channel),
            "provider": getattr(o.provider, "value", o.provider) if getattr(o, "provider", None) else None,
            "status": getattr(o.status, "value", o.status),

            "table_id": getattr(o, "table_id", None),
            "customer_id": getattr(o, "customer_id", None),
            "opened_by_user_id": getattr(o, "opened_by_user_id", None),
            "closed_by_user_id": getattr(o, "closed_by_user_id", None),

            "pax": getattr(o, "pax", None),
            "source_device_id": getattr(o, "source_device_id", None),
            "note": getattr(o, "note", None),

            # timestamps go out as ISO8601; FastAPI will handle datetime -> str for you
            "opened_at": getattr(o, "opened_at", None),
            "closed_at": getattr(o, "closed_at", None),
        })

    return {
        "items": items,
        "total": total,
    }


@router.post("/", response_model=OrderOut)
def open_order(body: OrderIn, db: Session = Depends(get_db), ctx: AuthCtx = Depends(require_auth)):
    # prepare kwargs while enforcing tenant/branch
    data = body.model_dump()
    if hasattr(Order, "tenant_id"):
        data["tenant_id"] = ctx.tenant_id
    if hasattr(Order, "branch_id"):
        data["branch_id"] = _effective_branch_id(db, ctx, data.get("branch_id"))

    o = Order(
        **data,
        status=OrderStatus.OPEN,
        opened_by_user_id=ctx.user_id,
        opened_at=datetime.now(timezone.utc),
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return OrderOut(id=o.id, status=o.status.value, **body.model_dump())


@router.patch("/{order_id}/status")
def update_order_status(
    order_id: str,
    body: OrderStatusUpdate,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Explicitly update the status of an Order.
    This can be used to move an order to VOID, or manually to KITCHEN, READY, etc.
    """
    o = _ensure_order_access(db, order_id, ctx)
    
    try:
        new_status_enum = OrderStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid status value")

    old_status_str = o.status.value
    
    # Don't allow changing a finalized order (unless voiding)
    if o.status in (OrderStatus.CLOSED, OrderStatus.VOID) and new_status_enum != OrderStatus.VOID:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot change status of a {old_status_str} order"
        )

    o.status = new_status_enum
    
    # If moving to a terminal state, set closed_at/by
    if new_status_enum in (OrderStatus.CLOSED, OrderStatus.VOID):
        if not o.closed_at:
            o.closed_at = datetime.now(timezone.utc)
        if hasattr(o, "closed_by_user_id"):
            o.closed_by_user_id = ctx.user_id
    
    # Log the explicit status change
    db.add(
        AuditLog(
            actor_user_id=ctx.user_id,
            entity="Order",
            entity_id=order_id,
            action="STATUS_CHANGE",
            reason=body.reason,
            before=old_status_str,
            after=new_status_enum.value,
        )
    )
    
    db.commit()
    db.refresh(o)
    
    return {
        "id": o.id,
        "status": o.status.value,
        "closed_at": o.closed_at
    }


@router.post("/{order_id}/items")
def add_item(order_id: str, body: OrderItemIn, db: Session = Depends(get_db), ctx: AuthCtx = Depends(require_auth)):
    if order_id != body.order_id:
        raise HTTPException(400, detail="order_id mismatch")

    order = _ensure_order_access(db, order_id, ctx)

    mitem: MenuItem | None = db.get(MenuItem, body.item_id)
    if not mitem:
        raise HTTPException(404, detail="menu item not found")
    # tenant check for item if present
    if hasattr(mitem, "tenant_id") and mitem.tenant_id != ctx.tenant_id:
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
                branch_id=order.branch_id,
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
def pay(order_id: str, body: PaymentIn, db: Session = Depends(get_db), ctx: AuthCtx = Depends(require_auth)):
    if order_id != body.order_id:
        raise HTTPException(400, detail="order_id mismatch")

    o = _ensure_order_access(db, order_id, ctx)

    p = Payment(**body.model_dump(), paid_at=datetime.now(timezone.utc))
    db.add(p)
    db.flush()

    # compute totals and close if fully paid (Phase-1: assume single payment closes order)
    totals = compute_bill(db, order_id)
    o.status = OrderStatus.CLOSED
    o.closed_at = datetime.now(timezone.utc)
    # mark who closed (cashier)
    if hasattr(o, "closed_by_user_id"):
        o.closed_by_user_id = ctx.user_id

    db.commit()
    return {
        "payment_id": p.id,
        "order_status": o.status.value,
        "totals": totals,
    }


@router.post("/{order_id}/invoice")
def create_invoice(
    order_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    o = _ensure_order_access(db, order_id, ctx)

    # Idempotency: if invoice already exists, return it
    existing = db.query(Invoice).filter(Invoice.order_id == order_id).first()
    if existing:
        return {
            "invoice_id": existing.id,
            "invoice_no": existing.invoice_no,
        }

    # Generate invoice_no like INV-YYYYMMDD-0001
    today = datetime.now(timezone.utc).date()
    prefix = f"INV-{today.strftime('%Y%m%d')}"

    base_q = db.query(func.count(Invoice.id)).filter(
        Invoice.invoice_no.like(f"{prefix}-%")
    )
    start_n = int(base_q.scalar() or 0)

    attempts = 0
    while attempts < 3:
        inv_no = f"{prefix}-{start_n + 1 + attempts:04d}"
        try:
            inv = Invoice(
                order_id=order_id,
                invoice_no=inv_no,
                invoice_dt=datetime.now(timezone.utc),
                reprint_count=0,  # first print = not a "reprint" yet
            )
            if hasattr(inv, "cashier_user_id"):
                inv.cashier_user_id = ctx.user_id

            db.add(inv)
            db.commit()
            db.refresh(inv)

            return {
                "invoice_id": inv.id,
                "invoice_no": inv.invoice_no,
            }
        except IntegrityError:
            db.rollback()
            attempts += 1

    raise HTTPException(409, detail="Could not allocate a unique invoice number")

@router.delete("/{order_id}/items/{order_item_id}")
def remove_item(
    order_id: str,
    order_item_id: str,
    reason: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    _ensure_order_access(db, order_id, ctx)

    line = db.get(OrderItem, order_item_id)
    if not line or line.order_id != order_id:
        raise HTTPException(404, detail="order item not found")

    # soft-delete if schema supports it
    if hasattr(line, "deleted_at"):
        line.deleted_at = datetime.now(timezone.utc)
    if hasattr(line, "void_reason"):
        line.void_reason = reason

    # reverse stock (BOM) – safe Decimal arithmetic
    recipes = db.query(RecipeBOM).filter(RecipeBOM.item_id == line.item_id).all()
    for r in recipes:
        rq = r.qty if isinstance(r.qty, Decimal) else Decimal(str(r.qty))
        lq = line.qty if isinstance(line.qty, Decimal) else Decimal(str(line.qty))
        db.add(
            StockMove(
                ingredient_id=r.ingredient_id,
                type=StockMoveType.ADJUST,  # add back
                qty_change=(rq * lq),
                reason=f"Cancel order item {order_item_id}",
                ref_order_id=order_id,
            )
        )

    # audit (optional)
    if "AuditLog" in globals():
        db.add(AuditLog(actor_user_id=ctx.user_id, entity="OrderItem", entity_id=order_item_id, action="CANCEL", reason=reason))

    db.commit()
    return {"ok": True}

@router.post("/{order_id}/items/{order_item_id}/apply_discount")
def apply_discount(
    order_id: str,
    order_item_id: str,
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    body: {discount: float, reason?: str}
    """
    _ensure_order_access(db, order_id, ctx)

    line = db.get(OrderItem, order_item_id)
    if not line or line.order_id != order_id:
        raise HTTPException(404, detail="order item not found")

    disc = body.get("discount", 0.0)
    # store as Decimal when possible
    if hasattr(line, "line_discount") and isinstance(getattr(type(line), "line_discount").type.asdecimal, bool):
        line.line_discount = Decimal(str(disc))
    else:
        line.line_discount = float(disc)

    if hasattr(line, "discount_reason"):
        line.discount_reason = body.get("reason")

    db.commit()
    return {"ok": True, "line_discount": float(line.line_discount or 0)}

@router.post("/{order_id}/void")
def void_order(
    order_id: str,
    reason: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    o = _ensure_order_access(db, order_id, ctx)

    # choose a suitable terminal status
    if hasattr(OrderStatus, "VOID"):
        o.status = OrderStatus.VOID
    elif hasattr(OrderStatus, "CANCELLED"):
        o.status = OrderStatus.CANCELLED
    else:
        o.status = OrderStatus.CLOSED
    o.closed_at = datetime.now(timezone.utc)
    if hasattr(o, "void_reason"):
        o.void_reason = reason

    if "AuditLog" in globals():
        db.add(AuditLog(actor_user_id=ctx.user_id, entity="Order", entity_id=order_id, action="VOID", reason=reason))

    db.commit()
    return {"id": o.id, "status": o.status.value}

@router.get("/{order_id}")
def get_order(order_id: str, db: Session = Depends(get_db), ctx: AuthCtx = Depends(require_auth)):
    o = _ensure_order_access(db, order_id, ctx)

    totals = compute_bill(db, order_id)
    paid = float(sum((p.amount or 0) for p in db.query(Payment).filter(Payment.order_id == order_id).all()))
    total = float(totals.get("total", 0.0))
    due = _money(total - paid)

    # NEW: Fetch items with details
    items_query = (
        db.query(OrderItem, MenuItem.name, ItemVariant.label)
        .join(MenuItem, OrderItem.item_id == MenuItem.id)
        .outerjoin(ItemVariant, OrderItem.variant_id == ItemVariant.id)
        .filter(OrderItem.order_id == order_id)
        .all()
    )

    parsed_items = []
    for row in items_query:
        line, item_name, variant_label = row
        parsed_items.append({
            "id": line.id,
            "order_id": line.order_id,
            "item_id": line.item_id,
            "name": item_name,
            "variant_id": line.variant_id,
            "variant_label": variant_label,
            "qty": float(line.qty),
            "unit_price": float(line.unit_price),
            "line_discount": float(line.line_discount),
            "gst_rate": float(line.gst_rate),
            "cgst": float(line.cgst),
            "sgst": float(line.sgst),
            "igst": float(line.igst),
            "taxable_value": float(line.taxable_value),
        })

    return {
        "id": o.id,
        "status": getattr(o.status, "value", str(o.status)),
        "tenant_id": getattr(o, "tenant_id", None),
        "branch_id": getattr(o, "branch_id", None),
        "order_no": getattr(o, "order_no", None),
        "items": parsed_items, # NEW
        "totals": {
            **totals,
            "paid": _money(paid),
            "due": due,
            "total_due": due,  # test expects this alias
        },
    }