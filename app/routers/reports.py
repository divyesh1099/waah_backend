from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, select, desc
from datetime import datetime, date, timezone, timedelta
from typing import List, Optional

from app.db import get_db
from app.deps import require_perm, require_auth, AuthCtx
from app.models.core import (
    ReportDailySales, ReportStockSnapshot,
    Order, OrderStatus, OrderItem, StockMove, StockMoveType,
    MenuItem, MenuCategory, Payment, PayMode, ItemVariant
)
from app.services.billing import _money, compute_bill

router = APIRouter(prefix="/reports", tags=["reports"]) 


@router.post("/daily_sales/refresh")
def refresh_daily_sales(day: date, branch_id: str, db: Session = Depends(get_db), sub: str = Depends(require_perm("MANAGER_APPROVE"))):
    start = datetime.combine(day, datetime.min.time()).replace(tzinfo=timezone.utc)
    end = datetime.combine(day, datetime.max.time()).replace(tzinfo=timezone.utc)

    orders = (
        db.query(Order)
        .filter(Order.branch_id == branch_id, Order.closed_at.isnot(None), Order.closed_at >= start, Order.closed_at <= end, Order.status == OrderStatus.CLOSED)
        .all()
    )

    # Aggregate by (channel, provider)
    buckets: dict[tuple[str | None, str | None], dict] = {}
    for o in orders:
        key = (o.channel.value if hasattr(o.channel, 'value') else str(o.channel), (o.provider.value if o.provider else None) if hasattr(o.provider, 'value') else (str(o.provider) if o.provider else None))
        b = buckets.setdefault(key, {"orders": 0, "subtotal": 0.0, "tax": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "discounts": 0.0, "total": 0.0})
        b["orders"] += 1
        # tax split from lines
        for l in db.query(OrderItem).filter(OrderItem.order_id == o.id).all():
            b["tax"] += float(l.cgst or 0) + float(l.sgst or 0) + float(l.igst or 0)
            b["cgst"] += float(l.cgst or 0)
            b["sgst"] += float(l.sgst or 0)
            b["igst"] += float(l.igst or 0)
        totals = compute_bill(db, o.id)
        b["subtotal"] += float(totals.get("subtotal", 0))
        b["total"] += float(totals.get("total", 0))
        # simplistic: discounts only from line_discount sums vs subtotal difference not computed here in detail

    # write snapshots
    for (channel, provider), vals in buckets.items():
        row = (
            db.query(ReportDailySales)
            .filter(ReportDailySales.date == day, ReportDailySales.branch_id == branch_id, ReportDailySales.channel == channel, ReportDailySales.provider == provider)
            .first()
        )
        payload = dict(
            date=day,
            tenant_id=db.query(Order).filter(Order.branch_id == branch_id).first().tenant_id if orders else "",
            branch_id=branch_id,
            channel=channel,
            provider=provider,
            orders_count=vals["orders"],
            gross=_money(vals["subtotal"]),
            tax=_money(vals["tax"]),
            cgst=_money(vals["cgst"]),
            sgst=_money(vals["sgst"]),
            igst=_money(vals["igst"]),
            discounts=0.0,
            net=_money(vals["total"]),
        )
        if not row:
            db.add(ReportDailySales(**payload))
        else:
            for k, v in payload.items():
                setattr(row, k, v)
    db.commit()
    return {"refreshed": True, "buckets": len(buckets)}


@router.post("/stock_snapshot/refresh")
def refresh_stock_snapshot(day: date, db: Session = Depends(get_db), sub: str = Depends(require_perm("MANAGER_APPROVE"))):
    start = datetime.combine(day, datetime.min.time()).replace(tzinfo=timezone.utc)
    end = datetime.combine(day, datetime.max.time()).replace(tzinfo=timezone.utc)

    # Inventory ids present in any move
    ing_ids = [row[0] for row in db.query(StockMove.ingredient_id).distinct().all()]

    for ing_id in ing_ids:
        opening = db.query(func.coalesce(func.sum(StockMove.qty_change), 0)).filter(StockMove.ingredient_id == ing_id, StockMove.created_at < start).scalar() or 0
        purchased = db.query(func.coalesce(func.sum(StockMove.qty_change), 0)).filter(StockMove.ingredient_id == ing_id, StockMove.created_at >= start, StockMove.created_at <= end, StockMove.type == StockMoveType.PURCHASE).scalar() or 0
        used = db.query(func.coalesce(func.sum(StockMove.qty_change), 0)).filter(StockMove.ingredient_id == ing_id, StockMove.created_at >= start, StockMove.created_at <= end, StockMove.type == StockMoveType.SALE).scalar() or 0
        closing = opening + purchased + used

        row = db.query(ReportStockSnapshot).filter(ReportStockSnapshot.at_date == day, ReportStockSnapshot.ingredient_id == ing_id).first()
        payload = dict(at_date=day, ingredient_id=ing_id, opening_qty=float(opening), purchased_qty=float(purchased), used_qty=float(used), closing_qty=float(closing))
        if not row:
            db.add(ReportStockSnapshot(**payload))
        else:
            for k, v in payload.items():
                setattr(row, k, v)
    db.commit()
    return {"refreshed": True, "ingredients": len(ing_ids)}


# --- NEW ENDPOINTS ---

@router.get("/sales")
def get_sales_report(
    start_dt: datetime,
    end_dt: datetime,
    tenant_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    group_by: str = "date",  # date, payment_mode
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Sales Report: Aggregate Net Sales, Tax, etc.
    filtered by date range, branch, tenant.
    """
    # Base query on Orders
    q = db.query(Order).filter(
        Order.status == OrderStatus.CLOSED,
        Order.closed_at >= start_dt,
        Order.closed_at <= end_dt,
    )

    if tenant_id:
        q = q.filter(Order.tenant_id == tenant_id)
    elif ctx.tenant_id:
        q = q.filter(Order.tenant_id == ctx.tenant_id)
        
    if branch_id:
        q = q.filter(Order.branch_id == branch_id)
    elif ctx.branch_id:
        q = q.filter(Order.branch_id == ctx.branch_id)

    orders = q.all()

    # We do simple Python aggregation for flexibility
    # Data struct: { key: { orders_count, gross, tax, net } }
    
    buckets = {}

    for o in orders:
        if group_by == "payment_mode":
            # Find payment for this order (simplified: take first payment mode or 'Mixed')
            payments = db.query(Payment).filter(Payment.order_id == o.id).all()
            if not payments:
                key = "Unpaid"
            elif len(set(p.mode for p in payments)) > 1:
                key = "Mixed"
            else:
                key = payments[0].mode.name
        else:
            # Default: Date (YYYY-MM-DD)
            key = o.closed_at.strftime("%Y-%m-%d")

        b = buckets.setdefault(key, {"count": 0, "gross": 0.0, "tax": 0.0, "net": 0.0})
        
        # Calculate totals for this order
        # (Ideally we'd store these on Order model to avoid re-calc)
        # We will use the 'totals' helper or just sum items if needed.
        # Check if totals are stored? No, compute_bill is expensive on loop.
        # Optimization: use Order items sum.
        
        # Faster approach: sum items
        items = db.query(OrderItem).filter(OrderItem.order_id == o.id).all()
        order_gross = sum(float(i.qty) * float(i.unit_price) - float(i.line_discount or 0) for i in items)
        order_tax = sum(float(i.cgst or 0) + float(i.sgst or 0) + float(i.igst or 0) for i in items)
        # Rounding issues possible, but okay for report v1
        
        b["count"] += 1
        b["gross"] += order_gross
        b["tax"] += order_tax
        b["net"] += (order_gross + order_tax) # Approx

    # Format result
    result = []
    for k, v in buckets.items():
        result.append({
            "label": k,
            "orders_count": v["count"],
            "gross": _money(v["gross"]),
            "tax": _money(v["tax"]),
            "net": _money(v["net"]),
        })

    # Sort by label (date)
    result.sort(key=lambda x: x["label"], reverse=(group_by == "date"))
    return result


@router.get("/items")
def get_item_sales_report(
    start_dt: datetime,
    end_dt: datetime,
    tenant_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Item Sales: Top selling items by Qty or Revenue.
    """
    q = (
        db.query(
            MenuItem.name,
            ItemVariant.label,
            func.sum(OrderItem.qty).label("total_qty"),
            func.sum(OrderItem.qty * OrderItem.unit_price).label("total_rev")
        )
        .join(Order, Order.id == OrderItem.order_id)
        .join(MenuItem, MenuItem.id == OrderItem.item_id)
        .outerjoin(ItemVariant, ItemVariant.id == OrderItem.variant_id)
        .filter(
            Order.status == OrderStatus.CLOSED,
            Order.closed_at >= start_dt,
            Order.closed_at <= end_dt
        )
    )

    if tenant_id:
        q = q.filter(Order.tenant_id == tenant_id)
    elif ctx.tenant_id:
        q = q.filter(Order.tenant_id == ctx.tenant_id)

    if branch_id:
        q = q.filter(Order.branch_id == branch_id)
    elif ctx.branch_id:
        q = q.filter(Order.branch_id == ctx.branch_id)

    q = q.group_by(MenuItem.name, ItemVariant.label)
    q = q.order_by(desc("total_rev")).limit(limit)

    rows = q.all()
    
    return [
        {
            "name": r.name,
            "variant": r.label,
            "qty": float(r.total_qty or 0),
            "revenue": _money(float(r.total_rev or 0)),
        }
        for r in rows
    ]


@router.get("/categories")
def get_category_sales_report(
    start_dt: datetime,
    end_dt: datetime,
    tenant_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Category Sales: Revenue per category.
    """
    q = (
        db.query(
            MenuCategory.name,
            func.sum(OrderItem.qty).label("total_qty"),
            func.sum(OrderItem.qty * OrderItem.unit_price).label("total_rev")
        )
        .join(Order, Order.id == OrderItem.order_id)
        .join(MenuItem, MenuItem.id == OrderItem.item_id)
        .join(MenuCategory, MenuCategory.id == MenuItem.category_id)
        .filter(
            Order.status == OrderStatus.CLOSED,
            Order.closed_at >= start_dt,
            Order.closed_at <= end_dt
        )
    )

    if tenant_id:
        q = q.filter(Order.tenant_id == tenant_id)
    elif ctx.tenant_id:
        q = q.filter(Order.tenant_id == ctx.tenant_id)
        
    if branch_id:
        q = q.filter(Order.branch_id == branch_id)
    elif ctx.branch_id:
        q = q.filter(Order.branch_id == ctx.branch_id)

    q = q.group_by(MenuCategory.name)
    q = q.order_by(desc("total_rev"))

    rows = q.all()

    return [
        {
            "category": r.name,
            "qty": float(r.total_qty or 0),
            "revenue": _money(float(r.total_rev or 0)),
        }
        for r in rows
    ]
