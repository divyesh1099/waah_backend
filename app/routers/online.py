# app/routers/online.py
# DROP-IN v1 — minimal online orders ingestion for Zomato/Swiggy

from __future__ import annotations
import os, json, math
from datetime import datetime, timezone
from typing import Any, Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, UniqueConstraint, func, and_
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_auth
from app.models.core import (
    OnlineProvider, OrderChannel, OrderStatus,
    Order, OrderItem, ItemVariant, MenuItem, MenuCategory,
    OnlineOrder,  # existing model (provider, provider_order_id, order_id, status)
)
from app.models.core import Base  # for new tables below
from app.models.common import IdMixin, TSMMixin
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Enum, Text, DateTime, Integer, ForeignKey, UniqueConstraint

router = APIRouter(prefix="/online", tags=["online"])

# ─────────────────────────────────────────────────────────────
# Small helper models (DB) to avoid altering existing tables
# ─────────────────────────────────────────────────────────────

class OnlineOutletMap(Base, IdMixin, TSMMixin):
    """
    Map aggregator outlet/store IDs to your Branch IDs.
    This lets us route a webhook to the correct branch automatically.
    """
    __tablename__ = "online_outlet_map"
    provider: Mapped[OnlineProvider] = mapped_column(Enum(OnlineProvider))
    provider_outlet_id: Mapped[str] = mapped_column(String(80))
    branch_id: Mapped[str] = mapped_column(String(36))
    __table_args__ = (UniqueConstraint("provider", "provider_outlet_id", name="uq_outlet_per_provider"),)

class OnlineOrderEvent(Base, IdMixin, TSMMixin):
    """
    Keep raw payloads for audit/debug (latest is used for listing).
    """
    __tablename__ = "online_order_event"
    provider: Mapped[OnlineProvider] = mapped_column(Enum(OnlineProvider))
    provider_order_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20), default="CREATED")  # CREATED/UPDATED/CANCELLED
    raw_json: Mapped[str] = mapped_column(Text)
    outlet_id: Mapped[str | None] = mapped_column(String(80))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

# ─────────────────────────────────────────────────────────────
# Normalized “least-features” shape used internally
# ─────────────────────────────────────────────────────────────

class NormItemIn(BaseModel):
    name: str
    qty: float = 1
    unit_price: float = 0.0

class NormOrderIn(BaseModel):
    provider: OnlineProvider
    provider_order_id: str
    outlet_id: Optional[str] = None  # aggregator outlet/store/location id
    placed_at: Optional[datetime] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    address: Optional[str] = None
    items: List[NormItemIn] = Field(default_factory=list)
    total: Optional[float] = None
    note: Optional[str] = None
    status: str = "RECEIVED"

def _env_token() -> str:
    return os.getenv("ONLINE_WEBHOOK_TOKEN", "changeme")

def _auth_ok(request: Request, token_q: Optional[str]) -> bool:
    return (request.headers.get("X-Online-Token") == _env_token()) or (token_q == _env_token())

def _now_tz() -> datetime:
    return datetime.now(timezone.utc)

# ─────────────────────────────────────────────────────────────
# “Tolerant” adapters — guess common keys, don’t crash
# NOTE: Official payloads vary; this is intentionally defensive.
# ─────────────────────────────────────────────────────────────

def _coerce_float(x: Any, default=0.0) -> float:
    try:
        if x is None: return default
        f = float(x)
        if math.isnan(f) or math.isinf(f): return default
        return f
    except Exception:
        return default

def _first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None

def adapt_zomato(payload: dict) -> NormOrderIn:
    # Try a few common shapes:
    order = payload.get("order") or payload
    o_id = str(_first(order.get("order_id"), order.get("id")))
    outlet = str(_first(order.get("restaurant_id"), order.get("outlet_id"), order.get("store_id")))
    placed = _first(order.get("created_at"), order.get("placed_at"), order.get("time"))
    if isinstance(placed, str):
        try:
            placed = datetime.fromisoformat(placed.replace("Z", "+00:00"))
        except Exception:
            placed = None

    cust = order.get("customer") or {}
    name = _first(cust.get("name"), cust.get("customer_name"))
    phone = _first(cust.get("phone"), cust.get("mobile"))
    addr = None
    delivery = order.get("delivery_address") or order.get("address") or {}
    if isinstance(delivery, dict):
        addr = ", ".join([str(v) for v in [delivery.get("line1"), delivery.get("line2"), delivery.get("city"), delivery.get("pincode")] if v])
    elif isinstance(delivery, str):
        addr = delivery

    items_raw = order.get("items") or order.get("order_items") or []
    items: List[NormItemIn] = []
    for it in items_raw:
        nm = _first(it.get("name"), it.get("item_name"), it.get("title")) or "Item"
        qty = _coerce_float(_first(it.get("qty"), it.get("quantity"), it.get("count")), 1)
        price = _coerce_float(_first(it.get("price"), it.get("item_price"), it.get("unit_price")), 0.0)
        items.append(NormItemIn(name=nm.strip(), qty=qty, unit_price=price))

    total = _coerce_float(_first(order.get("order_total"), order.get("total"), order.get("amount")), None)
    note = _first(order.get("note"), order.get("instructions"), order.get("special_instructions"))
    stat = str(_first(order.get("status"), "RECEIVED")).upper()

    return NormOrderIn(
        provider=OnlineProvider.ZOMATO,
        provider_order_id=o_id,
        outlet_id=outlet,
        placed_at=placed,
        customer_name=name,
        customer_phone=phone,
        address=addr,
        items=items,
        total=total,
        note=note,
        status=stat,
    )

def adapt_swiggy(payload: dict) -> NormOrderIn:
    o_id = str(_first(payload.get("order_id"), payload.get("id")))
    outlet = str(_first(payload.get("outlet_id"), payload.get("store_id"), payload.get("restaurant_id")))
    placed = _first(payload.get("created_at"), payload.get("placed_at"), payload.get("time"))
    if isinstance(placed, str):
        try:
            placed = datetime.fromisoformat(placed.replace("Z", "+00:00"))
        except Exception:
            placed = None

    cust = payload.get("customer") or {}
    name = _first(cust.get("name"), cust.get("customer_name"))
    phone = _first(cust.get("phone"), cust.get("mobile"))
    addr = None
    delivery = payload.get("delivery_address") or payload.get("address") or {}
    if isinstance(delivery, dict):
        addr = ", ".join([str(v) for v in [delivery.get("line1"), delivery.get("line2"), delivery.get("city"), delivery.get("pincode")] if v])
    elif isinstance(delivery, str):
        addr = delivery

    items_raw = payload.get("order_items") or payload.get("items") or []
    items: List[NormItemIn] = []
    for it in items_raw:
        nm = _first(it.get("name"), it.get("item_name"), it.get("title")) or "Item"
        qty = _coerce_float(_first(it.get("qty"), it.get("quantity"), it.get("count")), 1)
        price = _coerce_float(_first(it.get("price"), it.get("item_price"), it.get("unit_price")), 0.0)
        items.append(NormItemIn(name=nm.strip(), qty=qty, unit_price=price))

    total = _coerce_float(_first(payload.get("order_total"), payload.get("total"), payload.get("amount")), None)
    note = _first(payload.get("note"), payload.get("instructions"), payload.get("special_instructions"))
    stat = str(_first(payload.get("status"), "RECEIVED")).upper()

    return NormOrderIn(
        provider=OnlineProvider.SWIGGY,
        provider_order_id=o_id,
        outlet_id=outlet,
        placed_at=placed,
        customer_name=name,
        customer_phone=phone,
        address=addr,
        items=items,
        total=total,
        note=note,
        status=stat,
    )

# ─────────────────────────────────────────────────────────────
# Minimal webhook
# ─────────────────────────────────────────────────────────────

@router.post("/webhook/{provider_slug}")
async def webhook_orders(
    provider_slug: str,
    request: Request,
    db: Session = Depends(get_db),
    token: Optional[str] = Query(None),
):
    if not _auth_ok(request, token):
        raise HTTPException(401, detail="unauthorized")

    provider_slug = provider_slug.lower().strip()
    if provider_slug not in ("zomato", "swiggy"):
        raise HTTPException(400, detail="unsupported provider")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, detail="invalid json")

    # Normalize
    if provider_slug == "zomato":
        norm = adapt_zomato(payload)
    else:
        norm = adapt_swiggy(payload)

    if not norm.provider_order_id:
        raise HTTPException(400, detail="missing provider_order_id")

    # Idempotency: skip if we already have this (provider+id)
    exists = db.execute(
        select(OnlineOrder).where(
            OnlineOrder.provider == norm.provider,
            OnlineOrder.provider_order_id == norm.provider_order_id
        )
    ).scalar_one_or_none()

    if not exists:
        oo = OnlineOrder(
            provider=norm.provider,
            provider_order_id=norm.provider_order_id,
            order_id=None,
            status=norm.status or "RECEIVED",
        )
        db.add(oo)
        db.flush()  # get id
    else:
        # update status if changed, keep same row
        if norm.status and exists.status != norm.status:
            exists.status = norm.status
        oo = exists

    # persist raw event
    db.add(OnlineOrderEvent(
        provider=norm.provider,
        provider_order_id=norm.provider_order_id,
        kind="CREATED" if not exists else "UPDATED",
        raw_json=json.dumps(payload, ensure_ascii=False),
        outlet_id=norm.outlet_id,
        received_at=_now_tz(),
    ))
    db.commit()

    return {"ok": True, "online_order_id": oo.id, "status": oo.status}

# ─────────────────────────────────────────────────────────────
# Admin: map outlet to branch (so POS can filter per-branch)
# ─────────────────────────────────────────────────────────────

class OutletMapIn(BaseModel):
    provider: OnlineProvider
    provider_outlet_id: str
    branch_id: str

@router.post("/outlet-map/upsert")
def upsert_outlet_map(
    body: OutletMapIn,
    db: Session = Depends(get_db),
    _ = Depends(require_auth),
):
    row = db.execute(
        select(OnlineOutletMap).where(
            OnlineOutletMap.provider == body.provider,
            OnlineOutletMap.provider_outlet_id == body.provider_outlet_id
        )
    ).scalar_one_or_none()
    if row:
        row.branch_id = body.branch_id
    else:
        db.add(OnlineOutletMap(
            provider=body.provider,
            provider_outlet_id=body.provider_outlet_id,
            branch_id=body.branch_id
        ))
    db.commit()
    return {"ok": True}

# ─────────────────────────────────────────────────────────────
# List recent online orders for POS
# ─────────────────────────────────────────────────────────────

class OnlineOrderListItem(BaseModel):
    id: str
    provider: OnlineProvider
    provider_order_id: str
    status: str
    received_at: Optional[datetime]
    branch_id_hint: Optional[str] = None
    order_id: Optional[str] = None
    items_summary: str = ""
    total: Optional[float] = None

def _latest_event_for(db: Session, prov: OnlineProvider, poid: str) -> Optional[OnlineOrderEvent]:
    return db.execute(
        select(OnlineOrderEvent)
        .where(
            OnlineOrderEvent.provider == prov,
            OnlineOrderEvent.provider_order_id == poid
        )
        .order_by(OnlineOrderEvent.received_at.desc())
        .limit(1)
    ).scalar_one_or_none()

@router.get("/orders")
def list_online_orders(
    db: Session = Depends(get_db),
    provider: Optional[OnlineProvider] = Query(None),
    branch_id: Optional[str] = Query(None),  # will filter using outlet map, if available
    limit: int = Query(50, ge=1, le=200),
):
    q = select(OnlineOrder).order_by(OnlineOrder.created_at.desc()).limit(limit)
    if provider:
        q = select(OnlineOrder).where(OnlineOrder.provider == provider).order_by(OnlineOrder.created_at.desc()).limit(limit)

    rows = db.execute(q).scalars().all()
    out: List[OnlineOrderListItem] = []

    # build outlet->branch map in memory (single query)
    maps = { (m.provider, m.provider_outlet_id): m.branch_id
             for m in db.execute(select(OnlineOutletMap)).scalars().all() }

    for r in rows:
        ev = _latest_event_for(db, r.provider, r.provider_order_id)
        branch_hint = None
        total = None
        items_summary = ""
        if ev:
            try:
                pj = json.loads(ev.raw_json)
                # re-adapt (cheap) to extract items & outlet
                norm = adapt_zomato(pj) if r.provider == OnlineProvider.ZOMATO else adapt_swiggy(pj)
                if norm.outlet_id and (r.provider, norm.outlet_id) in maps:
                    branch_hint = maps[(r.provider, norm.outlet_id)]
                # tiny items summary
                items_summary = ", ".join([f"{int(i.qty)}× {i.name}" if i.qty.is_integer() else f"{i.qty:g}× {i.name}" for i in norm.items][:5])
                total = norm.total
            except Exception:
                pass

        # If branch filter is requested, skip non-matching hints unless the order is already linked to a concrete Order with branch
        if branch_id:
            include = False
            if branch_hint and branch_hint == branch_id:
                include = True
            elif r.order_id:
                ord_row = db.get(Order, r.order_id)
                include = bool(ord_row and ord_row.branch_id == branch_id)
            if not include:
                continue

        out.append(OnlineOrderListItem(
            id=r.id,
            provider=r.provider,
            provider_order_id=r.provider_order_id,
            status=r.status,
            received_at=r.created_at,
            branch_id_hint=branch_hint,
            order_id=r.order_id,
            items_summary=items_summary,
            total=total,
        ))
    return {"ok": True, "data": [o.model_dump() for o in out]}

# ─────────────────────────────────────────────────────────────
# OPTIONAL: Accept → create minimal POS Order (best-effort map)
# ─────────────────────────────────────────────────────────────

class AcceptOut(BaseModel):
    ok: bool
    order_id: str

def _ensure_online_placeholder_item(db: Session, tenant_id: str) -> tuple[MenuItem, ItemVariant]:
    cat = db.execute(select(MenuCategory).where(
        MenuCategory.tenant_id == tenant_id,
        MenuCategory.name == "Online"
    )).scalar_one_or_none()
    if not cat:
        cat = MenuCategory(tenant_id=tenant_id, branch_id=tenant_id, name="Online", position=999)  # branch_id not used on category
        db.add(cat); db.flush()

    item = db.execute(select(MenuItem).where(
        MenuItem.tenant_id == tenant_id,
        MenuItem.name == "Online Unmapped Item"
    )).scalar_one_or_none()
    if not item:
        item = MenuItem(
            tenant_id=tenant_id,
            name="Online Unmapped Item",
            description="Placeholder for unmapped online items",
            category_id=cat.id,
            tax_inclusive=True,
            gst_rate=5.0,
            is_active=True,
        )
        db.add(item); db.flush()

    var = db.execute(select(ItemVariant).where(
        ItemVariant.item_id == item.id,
        ItemVariant.is_default == True
    )).scalar_one_or_none()
    if not var:
        var = ItemVariant(item_id=item.id, label="default", base_price=0, is_default=True)
        db.add(var); db.flush()

    return item, var

def _find_menu_item_by_name(db: Session, tenant_id: str, name: str) -> tuple[Optional[MenuItem], Optional[ItemVariant]]:
    name_norm = name.strip().lower()
    mi = db.execute(
        select(MenuItem).where(
            MenuItem.tenant_id == tenant_id,
            func.lower(MenuItem.name) == name_norm
        )
    ).scalar_one_or_none()
    if not mi:
        return None, None
    var = db.execute(select(ItemVariant).where(
        ItemVariant.item_id == mi.id,
        ItemVariant.is_default == True
    )).scalar_one_or_none()
    return mi, var

@router.post("/accept/{online_id}")
def accept_online_into_pos(
    online_id: str,
    tenant_id: str = Query(...),
    branch_id: str = Query(...),
    db: Session = Depends(get_db),
    _ = Depends(require_auth),
):
    oo = db.get(OnlineOrder, online_id)
    if not oo:
        raise HTTPException(404, detail="online order not found")

    ev = _latest_event_for(db, oo.provider, oo.provider_order_id)
    if not ev:
        raise HTTPException(400, detail="no payload for this online order")

    payload = json.loads(ev.raw_json)
    norm = adapt_zomato(payload) if oo.provider == OnlineProvider.ZOMATO else adapt_swiggy(payload)

    # Build Order
    ord_row = Order(
        tenant_id=tenant_id,
        branch_id=branch_id,
        order_no=f"{oo.provider.name[0]}-{oo.provider_order_id}",
        channel=OrderChannel.ONLINE,
        provider=oo.provider,
        status=OrderStatus.OPEN,
        pax=None,
        note=norm.note,
        opened_at=norm.placed_at or datetime.now(timezone.utc),
    )
    db.add(ord_row); db.flush()

    # Try exact name matches; fallback to placeholder item
    placeholder_item, placeholder_var = _ensure_online_placeholder_item(db, tenant_id)

    for it in norm.items:
        mi, var = _find_menu_item_by_name(db, tenant_id, it.name)
        if not mi:
            mi, var = placeholder_item, placeholder_var
        db.add(OrderItem(
            order_id=ord_row.id,
            item_id=mi.id,
            variant_id=var.id if var else None,
            parent_line_id=None,
            qty=it.qty,
            unit_price=it.unit_price if mi is placeholder_item else (var.base_price if var else it.unit_price),
            line_discount=0,
            gst_rate=5.0,  # simplest default
            cgst=0, sgst=0, igst=0, taxable_value=0,
        ))

    oo.order_id = ord_row.id
    oo.status = "ACCEPTED"
    db.commit()
    return AcceptOut(ok=True, order_id=ord_row.id)
