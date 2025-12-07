from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, select, desc
from datetime import datetime, date, timezone, timedelta
from typing import List, Optional

from app.db import get_db
from app.deps import require_perm, require_auth, AuthCtx
from app.models.core import (
    Order, OrderStatus, OrderItem,
    MenuItem, MenuCategory, Payment, ItemVariant
)
from app.services.billing import _money
from app.services.reporting import (
    refresh_all_reports_for_day,
    refresh_daily_sales_snapshot,
    refresh_stock_snapshot_for_day,
)

router = APIRouter(prefix="/reports", tags=["reports"]) 


@router.post("/daily_sales/refresh")
def refresh_daily_sales(day: date, branch_id: str, db: Session = Depends(get_db), sub: str = Depends(require_perm("MANAGER_APPROVE"))):
    buckets = refresh_daily_sales_snapshot(db, day, branch_id)
    db.commit()
    return {"refreshed": True, "buckets": buckets}


@router.post("/stock_snapshot/refresh")
def refresh_stock_snapshot(day: date, db: Session = Depends(get_db), sub: str = Depends(require_perm("MANAGER_APPROVE"))):
    ing_count = refresh_stock_snapshot_for_day(db, day)
    db.commit()
    return {"refreshed": True, "ingredients": ing_count}


@router.post("/refresh_for_day")
def refresh_all_for_day(
    day: date | None = None,
    branch_ids: List[str] | None = Query(None),
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("MANAGER_APPROVE")),
):
    """
    Refresh all report snapshots (daily sales + stock) for the given day.
    If day is omitted, defaults to today.
    If branch_ids is omitted, refreshes all branches.
    """
    target_day = day or datetime.now(timezone.utc).date()
    stats = refresh_all_reports_for_day(db, target_day, branch_ids)
    db.commit()
    return {"refreshed": True, **stats}


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
