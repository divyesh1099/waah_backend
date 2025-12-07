"""
Helpers to build reporting snapshots that can be reused by both HTTP routes
and background schedulers.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import (
    Branch,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    ReportDailySales,
    ReportStockSnapshot,
    StockMove,
    StockMoveType,
)
from app.services.billing import _money, compute_bill


def refresh_daily_sales_snapshot(db: Session, day: date, branch_id: str) -> int:
    """
    Compute and upsert daily sales snapshot for a single branch.
    Returns number of (channel, provider) buckets processed.
    """
    start = datetime.combine(day, datetime.min.time()).replace(tzinfo=timezone.utc)
    end = datetime.combine(day, datetime.max.time()).replace(tzinfo=timezone.utc)

    orders = (
        db.query(Order)
        .filter(
            Order.branch_id == branch_id,
            Order.closed_at.isnot(None),
            Order.closed_at >= start,
            Order.closed_at <= end,
            Order.status == OrderStatus.CLOSED,
        )
        .all()
    )

    buckets: dict[tuple[str | None, str | None], dict] = {}
    for o in orders:
        key = (
            o.channel.value if hasattr(o.channel, "value") else str(o.channel),
            (o.provider.value if o.provider else None)
            if hasattr(o.provider, "value")
            else (str(o.provider) if o.provider else None),
        )
        b = buckets.setdefault(
            key,
            {
                "orders": 0,
                "subtotal": 0.0,
                "tax": 0.0,
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 0.0,
                "discounts": 0.0,
                "total": 0.0,
            },
        )
        b["orders"] += 1
        for line in db.query(OrderItem).filter(OrderItem.order_id == o.id).all():
            b["tax"] += float(line.cgst or 0) + float(line.sgst or 0) + float(line.igst or 0)
            b["cgst"] += float(line.cgst or 0)
            b["sgst"] += float(line.sgst or 0)
            b["igst"] += float(line.igst or 0)
        totals = compute_bill(db, o.id)
        b["subtotal"] += float(totals.get("subtotal", 0))
        b["total"] += float(totals.get("total", 0))

    tenant_id = (
        db.query(Order).filter(Order.branch_id == branch_id).first().tenant_id
        if orders
        else ""
    )

    for (channel, provider), vals in buckets.items():
        row = (
            db.query(ReportDailySales)
            .filter(
                ReportDailySales.date == day,
                ReportDailySales.branch_id == branch_id,
                ReportDailySales.channel == channel,
                ReportDailySales.provider == provider,
            )
            .first()
        )
        payload = dict(
            date=day,
            tenant_id=tenant_id,
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
    return len(buckets)


def refresh_stock_snapshot_for_day(db: Session, day: date) -> int:
    """
    Upsert stock snapshot for all ingredients for the given day.
    Returns ingredient count processed.
    """
    start = datetime.combine(day, datetime.min.time()).replace(tzinfo=timezone.utc)
    end = datetime.combine(day, datetime.max.time()).replace(tzinfo=timezone.utc)

    ing_ids = [row[0] for row in db.query(StockMove.ingredient_id).distinct().all()]

    for ing_id in ing_ids:
        opening = (
            db.query(func.coalesce(func.sum(StockMove.qty_change), 0))
            .filter(StockMove.ingredient_id == ing_id, StockMove.created_at < start)
            .scalar()
            or 0
        )
        purchased = (
            db.query(func.coalesce(func.sum(StockMove.qty_change), 0))
            .filter(
                StockMove.ingredient_id == ing_id,
                StockMove.created_at >= start,
                StockMove.created_at <= end,
                StockMove.type == StockMoveType.PURCHASE,
            )
            .scalar()
            or 0
        )
        used = (
            db.query(func.coalesce(func.sum(StockMove.qty_change), 0))
            .filter(
                StockMove.ingredient_id == ing_id,
                StockMove.created_at >= start,
                StockMove.created_at <= end,
                StockMove.type == StockMoveType.SALE,
            )
            .scalar()
            or 0
        )
        closing = opening + purchased + used

        row = (
            db.query(ReportStockSnapshot)
            .filter(ReportStockSnapshot.at_date == day, ReportStockSnapshot.ingredient_id == ing_id)
            .first()
        )
        payload = dict(
            at_date=day,
            ingredient_id=ing_id,
            opening_qty=float(opening),
            purchased_qty=float(purchased),
            used_qty=float(used),
            closing_qty=float(closing),
        )
        if not row:
            db.add(ReportStockSnapshot(**payload))
        else:
            for k, v in payload.items():
                setattr(row, k, v)
    return len(ing_ids)


def refresh_all_reports_for_day(
    db: Session, day: date, branch_ids: Iterable[str] | None = None
) -> dict:
    """
    Refresh all report snapshots for the given day.
    Optionally scope to a list of branch_ids.
    """
    if branch_ids is None:
        branch_ids = [row[0] for row in db.query(Branch.id).all()]

    daily_buckets = 0
    processed_branches = 0
    for bid in branch_ids:
        processed_branches += 1
        daily_buckets += refresh_daily_sales_snapshot(db, day, bid)

    ing_count = refresh_stock_snapshot_for_day(db, day)
    return {
        "day": day.isoformat(),
        "branches": processed_branches,
        "daily_sales_buckets": daily_buckets,
        "ingredients": ing_count,
    }
