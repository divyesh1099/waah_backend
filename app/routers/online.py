# app/routers/online.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date, timezone

from app.db import get_db
from app.deps import require_auth
from app.models.core import (
    OnlineOrder, OnlineProvider,
    Order, OrderStatus,
    ReportDailySales,  # pre-aggregated daily sales
)

router = APIRouter(prefix="/online", tags=["online"])


@router.post("/webhooks/{provider}")
def webhook(provider: str, body: dict, db: Session = Depends(get_db)):
    """
    Receive provider webhooks and create a minimal OnlineOrder + Order shell.
    Frontend or order ingestion service can enrich items/payments later.
    """
    prov = OnlineProvider(provider.upper()) if provider else OnlineProvider.CUSTOM

    # Create OnlineOrder row
    oo = OnlineOrder(provider=prov, provider_order_id=body.get("order_id"))
    db.add(oo)
    db.flush()

    # Create a skeleton Order
    o = Order(
        tenant_id=body.get("tenant_id"),
        branch_id=body.get("branch_id"),
        order_no=int(datetime.now().timestamp()),
        channel="ONLINE",
        provider=prov,
        status=OrderStatus.OPEN,
        opened_at=datetime.now(timezone.utc),
    )
    db.add(o)
    db.flush()

    # Link them
    oo.order_id = o.id

    db.commit()
    return {"online_order_id": oo.id, "order_id": o.id}


@router.post("/orders/{order_id}/status")
def set_online_status(
    order_id: str,
    status: str,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    Update an order's high-level status (e.g., READY, SERVED, CLOSED, etc.).
    Keeps compatibility with your earlier clients that call this route.
    """
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    try:
        o.status = OrderStatus(status)
    except Exception:
        raise HTTPException(400, "invalid status")
    db.commit()
    return {"id": order_id, "status": o.status.value}


# NOTE: This replaces the old on-the-fly SUM over payments.
# We now read from ReportDailySales, which your reports job populates.
@router.get("/report/sales")
def provider_sales(
    start: date,
    end: date,
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    Return pre-aggregated ONLINE sales by provider from ReportDailySales.
    - Sums `net` for amount and `orders_count` for bills.
    - Optional branch filter.
    """
    q = (
        db.query(
            ReportDailySales.provider,
            func.coalesce(func.sum(ReportDailySales.net), 0).label("amount"),
            func.coalesce(func.sum(ReportDailySales.orders_count), 0).label("bills"),
        )
        .filter(
            ReportDailySales.date >= start,
            ReportDailySales.date <= end,
            ReportDailySales.channel == "ONLINE",
        )
    )
    if branch_id:
        q = q.filter(ReportDailySales.branch_id == branch_id)

    rows = q.group_by(ReportDailySales.provider).all()

    return [
        {
            "provider": (prov or "UNKNOWN"),
            "amount": float(amount or 0),
            "bills": int(bills or 0),
        }
        for prov, amount, bills in rows
    ]
