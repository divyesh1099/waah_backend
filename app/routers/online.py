from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from app.db import get_db
from app.deps import require_auth
from app.models.core import OnlineOrder, OnlineProvider, Order, OrderStatus, Payment

router = APIRouter(prefix="/online", tags=["online"])

@router.post("/webhooks/{provider}")
def webhook(provider: str, body: dict, db: Session = Depends(get_db)):
    prov = OnlineProvider(provider.upper()) if provider else OnlineProvider.CUSTOM
    oo = OnlineOrder(provider=prov, provider_order_id=body.get("order_id"))
    db.add(oo); db.flush()
    o = Order(tenant_id=body.get("tenant_id"), branch_id=body.get("branch_id"),
              order_no=int(datetime.now().timestamp()), channel="ONLINE", provider=prov, status="OPEN")
    db.add(o); db.flush()
    oo.order_id = o.id
    db.commit()
    return {"online_order_id": oo.id, "order_id": o.id}

@router.get("/report/sales")
def provider_sales(start: str, end: str, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    # Totals by provider from payments linked to orders in that window
    rows = (db.query(Order.provider, func.coalesce(func.sum(Payment.amount), 0).label("amount"),
                     func.count(Payment.id).label("bills"))
              .join(Payment, Payment.order_id == Order.id)
              .filter(Order.closed_at >= start, Order.closed_at <= end, Order.channel == "ONLINE")
              .group_by(Order.provider).all())
    return [{"provider": p[0].name if p[0] else "UNKNOWN", "amount": float(p[1] or 0), "bills": int(p[2] or 0)} for p in rows]

@router.post("/orders/{order_id}/status")
def set_online_status(order_id: str, status: str, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    o.status = OrderStatus(status)
    db.commit()
    return {"id": order_id, "status": o.status.value}

@router.get("/report/sales")
def provider_sales(start: str, end: str, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    rows = (db.query(Order.provider, func.coalesce(func.sum(Payment.amount), 0).label("amount"),
                     func.count(Payment.id).label("bills"))
              .join(Payment, Payment.order_id == Order.id)
              .filter(Order.closed_at >= start, Order.closed_at <= end, Order.channel == "ONLINE")
              .group_by(Order.provider).all())
    return [{"provider": p[0].name if p[0] else "UNKNOWN", "amount": float(p[1] or 0), "bills": int(p[2] or 0)} for p in rows]
