# app/services/sync_apply.py
from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.core import (
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

def apply_ops(ops: Iterable[dict], *, db: Session, user_id: str, device_id: str | None) -> int:
    """
    Apply a subset of sync ops into domain tables.
    Currently supports:
      - entity="Order", op="UPSERT"
        Required in payload: tenant_id, branch_id, order_no
        Optional: channel, status, pax, note, opened_at, items: [...]
    """
    applied = 0

    for op in ops:
        entity = op.get("entity")
        if entity != "Order":
            # (future) handle other entities like OrderItem/Payment/KOT, etc.
            continue

        if op.get("op") not in ("UPSERT", "CREATE", "UPDATE"):
            continue

        payload = op.get("payload") or {}
        tenant_id   = payload.get("tenant_id")
        branch_id   = payload.get("branch_id")
        order_no    = payload.get("order_no")

        if not (tenant_id and branch_id and order_no):
            # insufficient info — skip rather than breaking the whole batch
            # (client must send these; FE has activeTenantId/activeBranchId)
            continue

        channel     = _enum(OrderChannel, payload.get("channel"), OrderChannel.TAKEAWAY)
        status      = _enum(OrderStatus,  payload.get("status"),  OrderStatus.OPEN)
        opened_at   = _parse_dt(payload.get("opened_at")) or datetime.now(timezone.utc)
        pax         = payload.get("pax")
        note        = payload.get("note")

        # Find existing order by (branch_id, order_no) for idempotency
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
            new_id = (payload.get("id")
                      or op.get("entity_id")
                      or str(uuid4()))
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
            db.flush()  # get row.id

        # Optional: upsert/replace basic line items if payload has them
        items = payload.get("items")
        if isinstance(items, list):
            # simplest, deterministic approach: replace all existing lines
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
