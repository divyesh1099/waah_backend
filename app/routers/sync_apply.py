from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any, Iterable, cast

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.core import (
    KOTStatus, KitchenTicket, KitchenTicketItem,
    Order, OrderItem, OrderChannel, OrderStatus
)

def _parse_dt(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        s = str(val)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _enum(enum_cls, value, default):
    if value is None:
        return default
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except Exception:
        try:
            return enum_cls(str(value).upper())
        except Exception:
            return default

def _apply_order(
    op: dict, *, db: Session, user_id: str, device_id: str | None
) -> tuple[str | None, dict[str, str]]:
    """
    Applies a single Order operation.
    Returns the (order_id, client_to_db_item_id_map)
    """
    payload = op.get("payload") or {}
    tenant_id = payload.get("tenant_id")
    branch_id = payload.get("branch_id")
    order_no = payload.get("order_no")
    if not (tenant_id and branch_id and order_no):
        return None, {}  # Skip this op

    channel = _enum(OrderChannel, payload.get("channel"), OrderChannel.TAKEAWAY)
    status = _enum(OrderStatus, payload.get("status"), OrderStatus.OPEN)
    opened_at = _parse_dt(payload.get("opened_at")) or datetime.now(timezone.utc)
    pax = payload.get("pax")
    note = payload.get("note")

    row = (
        db.query(Order)
        .filter(Order.branch_id == branch_id, Order.order_no == order_no)
        .first()
    )

    order_items_by_client_id = {}  # Store client_id -> db_id mapping

    if row:
        row.status = status
        row.channel = channel
        row.pax = pax if pax is not None else row.pax
        row.note = note if note is not None else row.note
        row.source_device_id = device_id or row.source_device_id
        if not row.opened_at:
            row.opened_at = opened_at
    else:
        new_id = (payload.get("id") or op.get("entity_id") or str(uuid4()))
        row = Order(
            id=new_id,
            tenant_id=tenant_id,
            branch_id=branch_id,
            order_no=order_no,
            channel=channel,
            status=status,
            opened_by_user_id=user_id,
            opened_at=opened_at,
            source_device_id=device_id,
            pax=pax,
            note=note,
        )
        db.add(row)
        db.flush() # We need row.id before creating items

    items = payload.get("items")
    if isinstance(items, list):
        db.query(OrderItem).filter(OrderItem.order_id == row.id).delete()
        for it in items:
            if not isinstance(it, dict):
                continue
            
            client_line_id = it.get("client_id")  # Get the client_id
            
            oi = OrderItem(
                id=str(uuid4()),
                order_id=row.id,
                item_id=it.get("item_id"),
                variant_id=it.get("variant_id"),
                parent_line_id=it.get("parent_line_id"),
                qty=it.get("qty", 1),
                unit_price=it.get("unit_price", 0.0),
                line_discount=it.get("line_discount", 0.0),
                gst_rate=it.get("gst_rate", 5.0),
                cgst=0, sgst=0, igst=0,
                taxable_value=round(float(it.get("qty", 1)) * float(it.get("unit_price", 0.0)), 2),
            )
            db.add(oi)
            db.flush()  # Flush to get oi.id
            
            if client_line_id:
                order_items_by_client_id[client_line_id] = oi.id

    return row.id, order_items_by_client_id

def _get_next_kot_number(db: Session, branch_id: str) -> int:
    """
    Finds the next sequential KOT number for a given branch.
    Relies on KitchenTicket being joined to Order to filter by branch.
    """
    max_no = (
        db.query(func.max(KitchenTicket.ticket_no))
          .select_from(KitchenTicket)
          .join(Order, Order.id == KitchenTicket.order_id)
          .filter(Order.branch_id == branch_id)
          .scalar()
    )
    return (max_no or 0) + 1

def _apply_kot(
    op: dict,
    *,
    db: Session,
    user_id: str,
    device_id: str | None,
    order_map: dict[str, str],
    item_map: dict[str, str],
) -> int:
    """
    Applies a single KitchenTicket operation.
    Uses the provided maps to link to the parent Order and OrderItems.
    """
    payload = op.get("payload") or {}

    # 1. Find the parent Order.id using the order_no_ref
    order_no_ref = payload.get("order_no_ref")
    if not order_no_ref:
        return 0  # Can't link KOT without this

    order_id = order_map.get(order_no_ref)  # Check the map of orders created in this *same* transaction
    if not order_id:
        # Not in this transaction, maybe it's an "add-on" KOT for an existing order
        order_row = db.query(Order.id).filter(
            Order.order_no == order_no_ref, 
            Order.branch_id == payload.get("branch_id")
        ).first()
        
        if order_row:
            order_id = order_row.id
        else:
            return 0  # Can't find parent order, skip KOT

    # 2. Get KOT details
    tenant_id = payload.get("tenant_id")
    branch_id = payload.get("branch_id")
    if not (tenant_id and branch_id):
        return 0  # Missing required fields

    # 3. Generate a new, sequential ticket number for this branch
    next_ticket_no = _get_next_kot_number(db, branch_id)

    # 4. Create the KitchenTicket
    kot = KitchenTicket(
        id=(op.get("entity_id") or str(uuid4())),
        order_id=order_id,
        ticket_no=next_ticket_no,  # Use the REAL, sequential ticket number
        target_station=payload.get("target_station"),
        status=_enum(KOTStatus, payload.get("status"), KOTStatus.NEW),
        printed_at=_parse_dt(payload.get("printed_at")) or datetime.now(timezone.utc),
    )
    db.add(kot)
    db.flush()  # Get kot.id

    # 5. Create KitchenTicketItems
    kot_lines = payload.get("lines")
    if isinstance(kot_lines, list):
        for line_payload in kot_lines:
            if not isinstance(line_payload, dict):
                continue
            
            order_item_client_id = line_payload.get("order_item_client_id")
            order_item_id = item_map.get(order_item_client_id)  # Find the DB ID for this item

            if not order_item_id:
                # This item wasn't in the same batch or couldn't be found.
                # In a real system, you might try to find it by other means,
                # but for now, we'll skip it.
                continue

            db.add(KitchenTicketItem(
                id=str(uuid4()),
                ticket_id=kot.id,
                order_item_id=order_item_id,  # Use the resolved DB ID
                qty=line_payload.get("qty", 1),
                note=", ".join(line_payload.get("modifiers", []) or []) # Combine modifiers
            ))

    return 1  # Applied 1 KOT op


# --- MAIN SYNC FUNCTION ---

def apply_ops(ops: Iterable[dict], *, db: Session, user_id: str, device_id: str | None) -> int:
    applied_count = 0
    
    # We must process Orders first, then KOTs, so we can link them.
    # We'll store mappings of (client_id -> db_id) for this transaction.
    
    order_id_map: dict[str, str] = {}  # "POS-123" (order_no) -> "uuid-db-order-id"
    order_item_map: dict[str, str] = {} # "li-123" (client_id) -> "uuid-db-item-id"

    # 1st Pass: Process Orders
    order_ops = [op for op in ops if (op.get("entity") or "").lower() == "order"]
    for op in order_ops:
        oper = (op.get("op") or "").upper()
        if oper not in ("UPSERT", "CREATE", "UPDATE", "OPEN"):
            continue
        
        try:
            order_id, new_item_map = _apply_order(op, db=db, user_id=user_id, device_id=device_id)
            if order_id:
                applied_count += 1
                order_no = op.get("payload", {}).get("order_no")
                if order_no:
                    order_id_map[order_no] = order_id  # Store the mapping
                if new_item_map:
                    order_item_map.update(new_item_map)  # Add all new item mappings
        except Exception as e:
            print(f"Failed to apply order op: {e}") # Log error
            pass  # Continue to next op

    # 2nd Pass: Process KitchenTickets
    kot_ops = [op for op in ops if (op.get("entity") or "").lower() == "kitchenticket"]
    for op in kot_ops:
        oper = (op.get("op") or "").upper()
        if oper not in ("UPSERT", "CREATE", "UPDATE", "OPEN"):
            continue
        
        try:
            applied = _apply_kot(
                op,
                db=db,
                user_id=user_id,
                device_id=device_id,
                order_map=order_id_map,
                item_map=order_item_map,
            )
            if applied > 0:
                applied_count += 1
        except Exception as e:
            print(f"Failed to apply KOT op: {e}") # Log error
            pass

        payload   = op.get("payload") or {}
        tenant_id = payload.get("tenant_id")
        branch_id = payload.get("branch_id")
        order_no  = payload.get("order_no")
        if not (tenant_id and branch_id and order_no):
            continue

        channel   = _enum(OrderChannel, payload.get("channel"), OrderChannel.TAKEAWAY)
        status    = _enum(OrderStatus,  payload.get("status"),  OrderStatus.OPEN)
        opened_at = _parse_dt(payload.get("opened_at")) or datetime.now(timezone.utc)
        pax       = payload.get("pax")
        note      = payload.get("note")

        # idempotent upsert by (branch_id, order_no)
        row = (db.query(Order)
                 .filter(Order.branch_id == branch_id, Order.order_no == order_no)
                 .first())

        if row:
            row.status = status
            row.channel = channel
            row.pax = pax if pax is not None else row.pax
            row.note = note if note is not None else row.note
            row.source_device_id = device_id or row.source_device_id
            if not row.opened_at:
                row.opened_at = opened_at
        else:
            new_id = (payload.get("id") or op.get("entity_id") or str(uuid4()))
            row = Order(
                id=new_id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                order_no=order_no,
                channel=channel,
                status=status,
                opened_by_user_id=user_id,
                opened_at=opened_at,
                source_device_id=device_id,
                pax=pax,
                note=note,
            )
            db.add(row)
            db.flush()

        # replace items if provided (keeps things deterministic)
        items = payload.get("items")
        if isinstance(items, list):
            db.query(OrderItem).filter(OrderItem.order_id == row.id).delete()
            for it in items:
                if not it:
                    continue
                db.add(OrderItem(
                    id=str(uuid4()),
                    order_id=row.id,
                    item_id=it.get("item_id"),
                    variant_id=it.get("variant_id"),
                    parent_line_id=it.get("parent_line_id"),
                    qty=it.get("qty", 1),
                    unit_price=it.get("unit_price", 0.0),
                    line_discount=it.get("line_discount", 0.0),
                    gst_rate=it.get("gst_rate", 5.0),
                    cgst=0, sgst=0, igst=0,
                    taxable_value=round(float(it.get("qty", 1)) * float(it.get("unit_price", 0.0)), 2),
                ))

        applied += 1

    return applied

def _next_kot_number(db: Session, branch_id: str) -> int:
    # Per-branch monotonically increasing; simple & safe
    last = (db.query(KitchenTicket)
              .filter(KitchenTicket.branch_id == branch_id)
              .order_by(KitchenTicket.number.desc())
              .first())
    return (last.number + 1) if last and last.number else 1
