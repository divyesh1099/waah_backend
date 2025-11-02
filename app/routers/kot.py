from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import httpx
from typing import List, Dict, Any, Optional

from app.db import get_db
from app.deps import AuthCtx, require_auth, require_perm
from app.models.core import (
    AuditLog,
    KitchenTicket,
    KitchenTicketItem,
    KitchenStation,
    KOTStatus,
    Order,
    OrderItem,
    OrderItemModifier,
    MenuItem,
    Modifier,
    DiningTable,
    User,
    Printer,
    MenuCategory,
)

router = APIRouter(prefix="/kot", tags=["kot"])


async def _post_agent(url: str, payload: dict):
    """
    Fire-and-forget POST to station's local/edge print agent.
    We swallow errors to not block POS flow.
    """
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(url, json=payload)
    except Exception:
        # printer might be offline; don't raise to client
        pass


def _ensure_order_access(db: Session, order_id: str, ctx: AuthCtx) -> Order:
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(status_code=404, detail="order not found")

    # tenant scope
    tenant_id = getattr(o, "tenant_id", None)
    if tenant_id is not None and tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="order not found")

    # branch scope
    if ctx.branch_id:
        order_branch = getattr(o, "branch_id", None)
        if order_branch is not None and order_branch != ctx.branch_id:
            raise HTTPException(status_code=404, detail="order not found")

    return o


def _ensure_ticket_access(db: Session, ticket_id: str, ctx: AuthCtx) -> KitchenTicket:
    t = db.get(KitchenTicket, ticket_id)
    if not t:
        raise HTTPException(status_code=404, detail="ticket not found")
    _ensure_order_access(db, t.order_id, ctx)
    return t


def _ensure_station_access(db: Session, station_id: Optional[str], ctx: AuthCtx) -> None:
    """
    Make sure the requested kitchen station (Indian / Chinese / etc) is valid
    and belongs to the same tenant/branch.
    """
    if not station_id:
        return
    st = db.get(KitchenStation, station_id)
    if not st:
        raise HTTPException(status_code=404, detail="station not found")

    st_tenant = getattr(st, "tenant_id", None)
    if st_tenant is not None and st_tenant != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="station not found")

    if ctx.branch_id:
        st_branch = getattr(st, "branch_id", None)
        if st_branch is not None and st_branch != ctx.branch_id:
            raise HTTPException(status_code=404, detail="station not found")


def _gather_station_lines(
    db: Session,
    order_id: str,
    station_id: Optional[str],
    ctx: Optional[AuthCtx] = None,
) -> List[Dict[str, Any]]:
    """
    Collect all order lines that belong to `station_id` (kitchen station),
    including variant label and modifiers as strings, ready for API payloads.
    Returned dict per line:
        {
            "order_item_id": "<uuid of order_item row>",
            "name": "Paneer Chilli",
            "qty": 2.0,
            "variantLabel": "Full",
            "mods": ["Extra Spicy", "No Onion x2"],
        }
    """

    # Base query: order items joined with menu items
    q = (
        db.query(
            OrderItem,
            MenuItem.name.label("item_name"),
            MenuItem.kitchen_station_id.label("station_id"),
        )
        .join(MenuItem, MenuItem.id == OrderItem.item_id)
        .filter(OrderItem.order_id == order_id)
    )

    # Enforce tenant/branch via MenuCategory so one branch can't leak into another
    if ctx is not None:
        q = q.join(MenuCategory, MenuCategory.id == MenuItem.category_id)
        q = q.filter(MenuCategory.tenant_id == ctx.tenant_id)
        if ctx.branch_id:
            q = q.filter(MenuCategory.branch_id == ctx.branch_id)

    # Limit lines to this specific kitchen station if provided
    if station_id:
        q = q.filter(MenuItem.kitchen_station_id == station_id)

    rows = q.all()

    out_lines: List[Dict[str, Any]] = []
    for line, item_name, _station_id in rows:
        # variant label (optional)
        vlabel: Optional[str] = None
        try:
            if getattr(line, "variant_id", None):
                v = db.get(ItemVariant, line.variant_id)
                if v and getattr(v, "label", None):
                    vlabel = v.label
        except Exception:
            pass

        # modifiers for this specific order_item line
        mods_q = (
            db.query(
                OrderItemModifier,
                Modifier.name.label("mod_name"),
            )
            .join(Modifier, Modifier.id == OrderItemModifier.modifier_id)
            .filter(OrderItemModifier.order_item_id == line.id)
        )
        mods_rows = mods_q.all()

        mods_list: List[str] = []
        for om, mod_name in mods_rows:
            qty_suffix = ""
            this_qty = getattr(om, "qty", 1)
            if this_qty and this_qty != 1:
                qty_suffix = f" x{this_qty}"
            mods_list.append(f"{mod_name}{qty_suffix}")

        out_lines.append(
            {
                "order_item_id": line.id,
                "name": item_name,
                "qty": float(getattr(line, "qty", 0) or 0),
                "variantLabel": vlabel,
                "mods": mods_list,
            }
        )

    return out_lines

def _build_kot_payload(
    db: Session,
    t: KitchenTicket,
    ctx: Optional[AuthCtx] = None,
) -> tuple[dict, Optional[str], Optional[str], Optional[str], Optional[str], Optional[Order]]:
    """
    Build the printable payload for this kitchen ticket `t`.
    Includes table, waiter, timestamp, and line items.
    """

    order = db.get(Order, t.order_id)
    if not order:
        # fallback payload if order somehow missing (shouldn't really happen)
        return (
            {
                "type": "KOT",
                "ticket_id": t.id,
                "ticket_no": t.ticket_no,
                "station": None,
                "order_no": None,
                "table": None,
                "waiter": None,
                "time": datetime.now(timezone.utc).isoformat(),
                "note": None,
                "lines": [],
            },
            None,
            None,
            None,
            None,
            None,
        )

    # dining table code on ticket
    table_code = None
    if order.table_id:
        tbl = db.get(DiningTable, order.table_id)
        if tbl:
            table_code = tbl.code

    # waiter name
    waiter_name = None
    if order.opened_by_user_id:
        u = db.get(User, order.opened_by_user_id)
        if u:
            waiter_name = u.name

    # station / printer
    station_name = None
    printer_url = None
    if t.target_station:
        st = db.get(KitchenStation, t.target_station)
        if st:
            station_name = st.name
            if st.printer_id:
                pr = db.get(Printer, st.printer_id)
                if pr and pr.connection_url:
                    printer_url = pr.connection_url

    # build line items for this station
    raw_lines = _gather_station_lines(db, t.order_id, t.target_station, ctx)

    # For the printed slip, we don't expose order_item_id
    line_payloads = [
        {
            "name": ln["name"],
            "qty": ln["qty"],
            "mods": ln["mods"],
        }
        for ln in raw_lines
    ]

    payload = {
        "type": "KOT",
        "ticket_id": t.id,
        "ticket_no": t.ticket_no,
        "station": station_name,
        "order_no": order.order_no,
        "table": table_code,
        "waiter": waiter_name,
        "time": datetime.now(timezone.utc).isoformat(),
        "note": order.note,
        "lines": line_payloads,
    }

    return payload, printer_url, station_name, table_code, waiter_name, order


@router.post("/tickets")
async def create_ticket(
    order_id: str,
    ticket_no: int,
    target_station: Optional[str] = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Create a kitchen ticket for a station (e.g. 'Indian', 'Chinese').
    1. Insert KitchenTicket
    2. Snapshot each line (OrderItem) into KitchenTicketItem with FK order_item_id
    3. Print
    4. Audit
    """

    # --- access checks ------------------------------------------------------
    _ensure_order_access(db, order_id, ctx)
    _ensure_station_access(db, target_station, ctx)

    now = datetime.now(timezone.utc)

    # --- create the ticket row ---------------------------------------------
    t = KitchenTicket(
        order_id=order_id,
        ticket_no=ticket_no,
        target_station=target_station,
        status=KOTStatus.NEW,
        printed_at=now,
    )
    db.add(t)
    db.commit()     # so t.id is persisted
    db.refresh(t)

    # --- snapshot the current lines for auditability -----------------------
    station_lines = _gather_station_lines(db, order_id, target_station, ctx)
    for ln in station_lines:
        # Save modifiers ("No Onion", "Extra Cheese") into note, comma separated.
        note_text = ", ".join(ln["mods"]) if ln["mods"] else None

        kti = KitchenTicketItem(
            ticket_id=t.id,
            order_item_id=ln["order_item_id"],   # <-- FIX: no more NULL
            qty=ln["qty"],
            note=note_text,
        )
        db.add(kti)

    # flush so we catch any constraint issues (like NOT NULL) before printing
    db.flush()

    # --- build payload and try to print ------------------------------------
    payload, printer_url, _station_name, _table_code, _waiter_name, _order = _build_kot_payload(
        db, t, ctx
    )

    if printer_url:
        await _post_agent(printer_url, payload)

    # --- audit log ---------------------------------------------------------
    db.add(
        AuditLog(
            actor_user_id=ctx.user_id,  # must not be NULL for audit_log.actor_user_id
            entity="KitchenTicket",
            entity_id=t.id,
            action="PRINT_KOT",
            reason=None,
            before=None,
            after=None,
        )
    )

    db.commit()

    return {
        "ticket_id": t.id,
        "status": t.status.name,
    }


@router.get("/tickets")
def list_tickets(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    List kitchen tickets for this tenant/branch, optionally filtered by status.
    Response is shaped for the Flutter KOT v3.2 page (camelCase + lines + timestamps).
    """

    q = db.query(KitchenTicket).join(Order, Order.id == KitchenTicket.order_id)

    # Tenant/branch scoping
    if hasattr(Order, "tenant_id"):
        q = q.filter(Order.tenant_id == ctx.tenant_id)
    if ctx.branch_id and hasattr(Order, "branch_id"):
        q = q.filter(Order.branch_id == ctx.branch_id)

    # Optional status filter (expects values like NEW / IN_PROGRESS / READY)
    if status:
        try:
            st_enum = KOTStatus[status]
        except KeyError:
            raise HTTPException(status_code=400, detail="bad status")
        q = q.filter(KitchenTicket.status == st_enum)

    q = q.order_by(KitchenTicket.ticket_no.desc())
    tickets = q.all()

    out: List[Dict[str, Any]] = []
    for t in tickets:
        # Pull order & station context
        order = db.get(Order, t.order_id)
        table_code: Optional[str] = None
        waiter_name: Optional[str] = None
        station_name: Optional[str] = None

        if order and getattr(order, "table_id", None):
            tbl = db.get(DiningTable, order.table_id)
            if tbl:
                table_code = tbl.code

        if order and getattr(order, "opened_by_user_id", None):
            u = db.get(User, order.opened_by_user_id)
            if u:
                waiter_name = u.name

        if getattr(t, "target_station", None):
            st = db.get(KitchenStation, t.target_station)
            if st:
                station_name = st.name

        # Lines for this station (API-friendly): include variantLabel + modifiers
        raw_lines = _gather_station_lines(db, t.order_id, t.target_station, ctx)
        line_payloads = [
            {
                "name": ln["name"],
                "qty": ln["qty"],
                "variantLabel": ln.get("variantLabel"),
                "modifiers": ln.get("mods", []),  # frontend accepts list[str]
            }
            for ln in raw_lines
        ]

        # createdAt: prefer order.opened_at, else ticket.printed_at, else ticket.created_at (from TSMMixin)
        created_dt = None
        try:
            created_dt = getattr(order, "opened_at", None) or getattr(t, "printed_at", None) or getattr(t, "created_at", None)
        except Exception:
            pass
        created_iso = created_dt.isoformat() if created_dt else None

        out.append(
            {
                # identifiers
                "id": t.id,
                "orderId": t.order_id,
                "ticketNo": t.ticket_no,

                # status & timing
                "status": t.status.name if t.status else None,
                "createdAt": created_iso,

                # station / table / waiter
                "stationName": station_name,
                "tableCode": table_code,
                "waiterName": waiter_name,

                # order decorations
                "orderNo": getattr(order, "order_no", None) if order else None,
                "orderNote": getattr(order, "note", None) if order else None,
                "channel": getattr(getattr(order, "channel", None), "name", None) if order else None,
                "provider": getattr(getattr(order, "provider", None), "name", None) if order else None,

                # lines (for card + details sheet)
                "lines": line_payloads,
            }
        )

    return out

@router.patch("/{ticket_id}")
def update_ticket_status(
    ticket_id: str,
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Update kitchen ticket status (NEW → IN_PROGRESS → READY → DONE).
    """

    t = _ensure_ticket_access(db, ticket_id, ctx)

    new_status = body.get("status")
    if new_status:
        try:
            t.status = KOTStatus[new_status]
        except KeyError:
            raise HTTPException(status_code=400, detail="bad status")

    db.add(
        AuditLog(
            actor_user_id=ctx.user_id,
            entity="KitchenTicket",
            entity_id=ticket_id,
            action="STATUS_CHANGE",
            reason=None,
            before=None,
            after=new_status,
        )
    )

    db.commit()
    db.refresh(t)

    return {
        "id": t.id,
        "status": t.status.name if t.status else None,
    }


@router.post("/{ticket_id}/reprint")
async def reprint_ticket(
    ticket_id: str,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("REPRINT")),
):
    """
    Reprint a kitchen ticket (KOT). Increments reprint_count.
    """

    t = _ensure_ticket_access(db, ticket_id, ctx)

    payload, printer_url, _, _, _, _ = _build_kot_payload(db, t, ctx)
    payload["reprint"] = True

    if printer_url:
        await _post_agent(printer_url, payload)

    # bump counter
    if hasattr(t, "reprint_count"):
        t.reprint_count = (t.reprint_count or 0) + 1

    db.add(
        AuditLog(
            actor_user_id=ctx.user_id,
            entity="KitchenTicket",
            entity_id=ticket_id,
            action="REPRINT",
            reason=reason,
            before=None,
            after=None,
        )
    )

    db.commit()

    return {
        "ok": True,
        "reprint_count": getattr(t, "reprint_count", None),
    }


@router.post("/{ticket_id}/cancel")
def cancel_ticket(
    ticket_id: str,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("VOID")),
):
    """
    Cancel a KOT. Marks status -> CANCELLED and records the reason.
    """

    t = _ensure_ticket_access(db, ticket_id, ctx)

    t.status = KOTStatus.CANCELLED
    if hasattr(t, "cancel_reason"):
        t.cancel_reason = reason

    db.add(
        AuditLog(
            actor_user_id=ctx.user_id,
            entity="KitchenTicket",
            entity_id=ticket_id,
            action="CANCEL",
            reason=reason,
            before=None,
            after=None,
        )
    )

    db.commit()

    return {"ok": True, "status": "CANCELLED"}
