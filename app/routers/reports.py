from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, select
from datetime import datetime, date, timezone, timedelta

from app.db import get_db
from app.deps import require_perm, require_auth
from app.models.core import (
    ReportDailySales, ReportStockSnapshot,
    Order, OrderStatus, OrderItem, StockMove, StockMoveType
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
