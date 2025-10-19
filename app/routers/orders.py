from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone
from app.db import get_db
from app.schemas.orders import OrderIn, OrderOut, OrderItemIn, PaymentIn
from app.models.core import Order, OrderStatus, OrderItem, Payment, MenuItem, KitchenTicket, KitchenTicketItem, RecipeBOM, StockMove, StockMoveType
from app.deps import require_auth, require_perm, has_perm
from app.services.billing import compute_bill
from app.util.audit import audit

router = APIRouter(prefix="/orders", tags=["orders"])

@router.post("/", response_model=OrderOut)
def open_order(body: OrderIn, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    o = Order(**body.model_dump(), status=OrderStatus.OPEN, opened_by_user_id=sub, opened_at=datetime.now(timezone.utc))
    db.add(o); db.commit(); db.refresh(o)
    audit(db, sub, "order", o.id, "OPEN")
    db.commit()
    return OrderOut(id=o.id, status=o.status.value, **body.model_dump())

@router.post("/{order_id}/items")
def add_item(order_id: str, body: OrderItemIn, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    if order_id != body.order_id:
        raise HTTPException(400, detail="order_id mismatch")
    mitem = db.get(MenuItem, body.item_id)
    if not mitem:
        raise HTTPException(404, detail="menu item not found")
    if mitem.stock_out:
        raise HTTPException(409, detail="item out of stock")
    line = OrderItem(**body.model_dump(), gst_rate=mitem.gst_rate)
    db.add(line); db.flush()
    # Inventory deduction via BOM
    for r in db.query(RecipeBOM).filter(RecipeBOM.item_id==mitem.id).all():
        db.add(StockMove(ingredient_id=r.ingredient_id, type=StockMoveType.SALE, qty_change=-(r.qty*body.qty),
                         reason=f"Order {order_id}", ref_order_id=order_id))
    # Auto-KOT per station
    if mitem.kitchen_station_id:
        station_ticket = (db.query(KitchenTicket)
                            .filter(KitchenTicket.order_id==order_id,
                                    KitchenTicket.target_station==mitem.kitchen_station_id).first())
        if not station_ticket:
            station_ticket = KitchenTicket(order_id=order_id, ticket_no=int(datetime.now().timestamp()),
                                           target_station=mitem.kitchen_station_id)
            db.add(station_ticket); db.flush()
        db.add(KitchenTicketItem(ticket_id=station_ticket.id, order_item_id=line.id, qty=body.qty))
    audit(db, sub, "order_item", line.id, "ADD_LINE", after={"qty": body.qty, "unit_price": body.unit_price})
    db.commit()
    return {"id": line.id}

@router.post("/{order_id}/items/{line_id}/discount")
def discount_line(order_id: str, line_id: str, discount: float = Body(..., embed=True),
                  db: Session = Depends(get_db), sub: str = Depends(require_perm("DISCOUNT"))):
    line = db.get(OrderItem, line_id)
    if not line or line.order_id != order_id:
        raise HTTPException(404, "line not found")
    before = {"line_discount": float(line.line_discount or 0)}
    line.line_discount = discount
    audit(db, sub, "order_item", line.id, "DISCOUNT", before=before, after={"line_discount": float(discount)})
    db.commit()
    return {"id": line.id, "line_discount": float(line.line_discount or 0)}

@router.post("/{order_id}/void")
def void_order(order_id: str, reason: str = Body(..., embed=True),
               db: Session = Depends(get_db), sub: str = Depends(require_perm("VOID"))):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    o.status = OrderStatus.VOID
    o.closed_at = datetime.now(timezone.utc)
    audit(db, sub, "order", order_id, "VOID", reason=reason)
    db.commit()
    return {"id": order_id, "status": "VOID"}

@router.post("/{order_id}/pay")
def pay(order_id: str, body: PaymentIn, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    if order_id != body.order_id:
        raise HTTPException(400, detail="order_id mismatch")
    p = Payment(**body.model_dump(), paid_at=datetime.now(timezone.utc))
    db.add(p); db.flush()
    totals = compute_bill(db, order_id)
    o = db.get(Order, order_id)
    if o:
        o.status = OrderStatus.CLOSED
        o.closed_at = datetime.now(timezone.utc)
    audit(db, sub, "order", order_id, "PAY", after={"amount": body.amount, "mode": body.mode, "totals": totals})
    db.commit()
    return {"payment_id": p.id, "order_status": o.status.value if o else None, "totals": totals}

@router.post("/{order_id}/invoice")
def create_invoice(order_id: str, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    from app.models.core import Invoice
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, detail="order not found")
    inv_no = f"INV-{int(datetime.now().timestamp())}"
    inv = Invoice(order_id=order_id, invoice_no=inv_no, invoice_dt=datetime.now(timezone.utc))
    db.add(inv); db.commit(); db.refresh(inv)
    audit(db, sub, "invoice", inv.id, "CREATE")
    return {"invoice_id": inv.id, "invoice_no": inv.invoice_no}


@router.post("/{order_id}/items/{line_id}/discount")
def discount_line(order_id: str, line_id: str, discount: float = Body(..., embed=True),
                  db: Session = Depends(get_db), sub: str = Depends(require_perm("DISCOUNT"))):
    line = db.get(OrderItem, line_id)
    if not line or line.order_id != order_id:
        raise HTTPException(404, "line not found")
    before = {"line_discount": float(line.line_discount or 0)}
    line.line_discount = discount
    audit(db, sub, "order_item", line.id, "DISCOUNT", before=before, after={"line_discount": float(discount)})
    db.commit()
    return {"id": line.id, "line_discount": float(line.line_discount or 0)}

@router.post("/{order_id}/void")
def void_order(order_id: str, reason: str = Body(..., embed=True),
               db: Session = Depends(get_db), sub: str = Depends(require_perm("VOID"))):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    o.status = OrderStatus.VOID
    o.closed_at = datetime.now(timezone.utc)
    audit(db, sub, "order", order_id, "VOID", reason=reason)
    db.commit()
    return {"id": order_id, "status": "VOID"}
