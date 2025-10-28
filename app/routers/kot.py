from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone
import httpx

from app.db import get_db
from app.deps import AuthCtx, require_auth, require_perm
from app.models.core import (
    AuditLog,
    KitchenTicket,
    KitchenTicketItem,
    KitchenStation,
    KitchenTicketItem,
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
    # tenant scope (if model has tenant_id)
    tenant_id = getattr(o, "tenant_id", None)
    if tenant_id is not None and tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="order not found")
    # branch scope (if model has branch_id and ctx has one)
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


def _ensure_station_access(db: Session, station_id: str | None, ctx: AuthCtx) -> None:
    if not station_id:
        return
    st = db.get(KitchenStation, station_id)
    if not st:
        raise HTTPException(status_code=404, detail="station not found")
    # tenant/branch checks if present on model
    st_tenant = getattr(st, "tenant_id", None)
    if st_tenant is not None and st_tenant != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="station not found")
    if ctx.branch_id:
        st_branch = getattr(st, "branch_id", None)
        if st_branch is not None and st_branch != ctx.branch_id:
            raise HTTPException(status_code=404, detail="station not found")


def _gather_station_lines(db: Session, order_id: str, station_id: str | None, ctx: AuthCtx | None = None):
    """
    Collect KOT line items for a specific kitchen station.
    Each line has:
      - item name
      - qty
      - modifiers list (extra cheese, no onion, etc.)
    """
    q = (
        db.query(
            OrderItem,
            MenuItem.name.label("item_name"),
            MenuItem.kitchen_station_id.label("station_id"),
        )
        .join(MenuItem, MenuItem.id == OrderItem.item_id)
        .filter(OrderItem.order_id == order_id)
    )

    # Restrict to tenant/branch via item's category (MenuItem has no branch_id)
    if ctx is not None:
        q = q.join(MenuCategory, MenuCategory.id == MenuItem.category_id)
        q = q.filter(MenuCategory.tenant_id == ctx.tenant_id)
        if ctx.branch_id:
            q = q.filter(MenuCategory.branch_id == ctx.branch_id)

    # Only send lines that belong to this station (Indian, Chinese, etc.)
    if station_id:
        q = q.filter(MenuItem.kitchen_station_id == station_id)

    rows = q.all()

    lines_payload = []
    for line, item_name, _station_id in rows:
        # modifiers for this line
        mods_q = (
            db.query(
                OrderItemModifier,
                Modifier.name.label("mod_name"),
            )
            .join(Modifier, Modifier.id == OrderItemModifier.modifier_id)
            .filter(OrderItemModifier.order_item_id == line.id)
        )
        mods_rows = mods_q.all()

        mods_list: list[str] = []
        for om, mod_name in mods_rows:
            # include qty if >1, keep it simple for kitchen
            qty_suffix = ""
            if getattr(om, "qty", 1) and getattr(om, "qty", 1) != 1:
                qty_suffix = f" x{om.qty}"
            mods_list.append(f"{mod_name}{qty_suffix}")

        lines_payload.append(
            {
                "name": item_name,
                "qty": float(line.qty or 0),
                "mods": mods_list,
            }
        )

    return lines_payload


def _build_kot_payload(db: Session, t: KitchenTicket, ctx: AuthCtx | None = None) -> dict:
    """
    Build the payload we send to the kitchen printer for this ticket.
    Includes table, waiter, time, and notes.
    """

    order = db.get(Order, t.order_id)
    if not order:
        # minimal fallback
        return {
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
        }, None, None, None, None, None

    # table code
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

    # station info
    station = None
    station_name = None
    printer_url = None
    if t.target_station:
        station = db.get(KitchenStation, t.target_station)
        if station:
            station_name = station.name
            if station.printer_id:
                pr = db.get(Printer, station.printer_id)
                if pr and pr.connection_url:
                    printer_url = pr.connection_url

    # build line items only for this station (with tenant/branch scope if ctx given)
    line_payloads = _gather_station_lines(db, t.order_id, t.target_station, ctx)

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
    target_station: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Create a kitchen ticket for a station (e.g. 'Indian', 'Chinese').
    Immediately print to that station's printer.
    """

    # scope checks
    _ensure_order_access(db, order_id, ctx)
    _ensure_station_access(db, target_station, ctx)

    now = datetime.now(timezone.utc)

    t = KitchenTicket(
        order_id=order_id,
        ticket_no=ticket_no,
        target_station=target_station,
        status=KOTStatus.NEW,
        printed_at=now,
    )
    db.add(t)
    db.commit()
    db.refresh(t)

    # snapshot ticket lines into KitchenTicketItem rows for audit
    station_lines = _gather_station_lines(db, order_id, target_station, ctx)
    for ln in station_lines:
        kti = KitchenTicketItem(
            ticket_id=t.id,
            # we don't strictly need to copy order_item_id here unless we want,
            # but we'll leave order_item_id null-safe to avoid FK break if not known.
            order_item_id=None,
            qty=ln["qty"],
            note=None,
        )
        db.add(kti)

    # build payload and send to printer agent
    payload, printer_url, station_name, table_code, waiter_name, order = _build_kot_payload(
        db, t, ctx
    )

    if printer_url:
        await _post_agent(printer_url, payload)

    # audit log (PRINT_KOT)
    db.add(
        AuditLog(
            actor_user_id=str(getattr(ctx, "user_id", "")),
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
    status: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    List kitchen tickets, optionally filtered by status.
    Used by the kitchen screen (New / In Progress / Ready).
    """

    # Scope tickets via their orders to caller's tenant/branch
    q = db.query(KitchenTicket).join(Order, Order.id == KitchenTicket.order_id)
    if hasattr(Order, "tenant_id"):
        q = q.filter(Order.tenant_id == ctx.tenant_id)
    if ctx.branch_id and hasattr(Order, "branch_id"):
        q = q.filter(Order.branch_id == ctx.branch_id)

    if status:
        try:
            st_enum = KOTStatus[status]
        except KeyError:
            raise HTTPException(400, detail="bad status")
        q = q.filter(KitchenTicket.status == st_enum)

    # show newest first
    q = q.order_by(KitchenTicket.ticket_no.desc())
    tickets = q.all()

    out = []
    for t in tickets:
        payload, _printer_url, station_name, table_code, waiter_name, order = _build_kot_payload(
            db, t, ctx
        )

        out.append(
            {
                "id": t.id,
                "order_id": t.order_id,
                "ticket_no": t.ticket_no,
                "target_station": t.target_station,
                "station_name": station_name,
                "status": t.status.name if t.status else None,
                "printed_at": t.printed_at.isoformat() if t.printed_at else None,
                "reprint_count": t.reprint_count,
                "table_code": table_code,
                "waiter_name": waiter_name,
                "order_no": order.order_no if order else None,
                "order_note": order.note if order else None,
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
            raise HTTPException(400, detail="bad status")

    db.add(
        AuditLog(
            actor_user_id=str(getattr(ctx, "user_id", "")),
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
async def reprint(
    ticket_id: str,
    reason: str | None = None,
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

    # bump reprint_count + audit
    if hasattr(t, "reprint_count"):
        t.reprint_count = (t.reprint_count or 0) + 1

    db.add(
        AuditLog(
            actor_user_id=str(getattr(ctx, "user_id", "")),
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
def cancel(
    ticket_id: str,
    reason: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("VOID")),
):
    """
    Cancel a kitchen ticket. Status -> CANCELLED.
    """

    t = _ensure_ticket_access(db, ticket_id, ctx)

    t.status = KOTStatus.CANCELLED
    if hasattr(t, "cancel_reason"):
        t.cancel_reason = reason

    db.add(
        AuditLog(
            actor_user_id=str(getattr(ctx, "user_id", "")),
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
