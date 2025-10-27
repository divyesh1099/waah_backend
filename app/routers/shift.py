from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.db import get_db
from app.deps import require_auth, require_perm, AuthCtx
from app.models.core import Shift, CashMovement
from app.util.audit import audit

router = APIRouter(prefix="/shift", tags=["shift"])


def _user_has_perm(db: Session, user_id: str, code: str) -> bool:
    """
    Check if a user has a permission code via any of their roles.
    Used for manager override on cash mismatch.
    """
    from app.models.core import Permission, RolePermission, Role, UserRole
    perms = (
        db.query(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == user_id)
        .all()
    )
    return code in {p[0] for p in perms}


def _assert_branch_ctx(ctx: AuthCtx, branch_id: str):
    """
    Enforce branch scoping: if ctx has a branch_id, it must match the requested one.
    Hide mismatches as 404 to avoid leaking info across branches.
    """
    if ctx.branch_id and ctx.branch_id != branch_id:
        raise HTTPException(404, detail="not found")


def _assert_shift_ctx(ctx: AuthCtx, s: Shift | None):
    """
    For shift-id based routes, ensure the loaded shift belongs to the caller's branch.
    """
    if not s:
        raise HTTPException(404, detail="shift not found")
    if ctx.branch_id and s.branch_id != ctx.branch_id:
        raise HTTPException(404, detail="shift not found")


@router.get("/status", summary="Current shift for this branch")
def shift_status(
    branch_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Returns the *active* (unlocked) shift for this branch with movements
    and calculated expected cash right now.
    If no open shift, returns {}.
    """
    _assert_branch_ctx(ctx, branch_id)

    s = (
        db.query(Shift)
        .filter(
            Shift.branch_id == branch_id,
            Shift.locked == False,          # still open
        )
        .order_by(Shift.opened_at.desc())
        .first()
    )

    if not s:
        return {}

    # get movements (cash in/out)
    moves = (
        db.query(CashMovement)
        .filter(CashMovement.shift_id == s.id)
        .order_by(CashMovement.created_at.asc())
        .all()
    )

    payins = sum(float(m.amount or 0) for m in moves if m.kind == "PAYIN")
    payouts = sum(float(m.amount or 0) for m in moves if m.kind == "PAYOUT")

    expected_now = float(s.opening_float or 0) + payins - payouts

    return {
        "id": s.id,
        "branch_id": s.branch_id,
        "opened_at": s.opened_at,
        "opening_float": float(s.opening_float or 0),
        "expected_now": expected_now,
        "is_open_and_unlocked": (not s.locked),
        "movements": [
            {
                "id": m.id,
                "kind": m.kind,  # PAYIN or PAYOUT
                "amount": float(m.amount or 0),
                "reason": m.reason,
                "ts": m.created_at,
            }
            for m in moves
        ],
    }

@router.post("/open")
def open_shift(
    branch_id: str,
    opening_float: float = 0.0,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Start today's cash drawer / shift for this branch.
    Only 1 unlocked shift per branch.
    """
    _assert_branch_ctx(ctx, branch_id)

    existing = (
        db.query(Shift)
        .filter(Shift.branch_id == branch_id, Shift.locked.is_(False))
        .first()
    )
    if existing:
        raise HTTPException(409, detail="Shift already open for this branch")

    s = Shift(
        branch_id=branch_id,
        opened_by=ctx.user_id,
        opened_at=datetime.now(timezone.utc),
        opening_float=opening_float,
        locked=False,
    )
    db.add(s)
    db.commit()
    db.refresh(s)

    audit(
        db,
        ctx.user_id,
        "shift",
        s.id,
        "OPEN",
        after={"opening_float": opening_float},
    )
    db.commit()

    return {"shift_id": s.id}


@router.get("/current")
def current_shift(
    branch_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Return details of the active (not locked) shift for this branch,
    including cash movements and running expected cash.

    Response:
    {
      "shift_id": "...",        # or null if none open
      "branch_id": "...",
      "opened_at": "...",
      "opening_float": 500.0,
      "locked": false,
      "expected_now": 620.0,
      "closed_at": null,
      "expected_cash_final": null,
      "actual_cash_final": null,
      "close_note": null,
      "movements": [...]
    }
    """
    _assert_branch_ctx(ctx, branch_id)

    s = (
        db.query(Shift)
        .filter(Shift.branch_id == branch_id, Shift.locked.is_(False))
        .order_by(Shift.opened_at.desc())
        .first()
    )

    if not s:
        # no open shift right now
        return {"shift_id": None, "branch_id": branch_id, "movements": []}

    mov_rows = (
        db.query(CashMovement)
        .filter(CashMovement.shift_id == s.id)
        .order_by(CashMovement.created_at.asc())
        .all()
    )

    total_in = sum(m.amount for m in mov_rows if m.kind == "PAYIN")
    total_out = sum(m.amount for m in mov_rows if m.kind == "PAYOUT")

    # NOTE: you probably also want to include CASH payments from /orders/pay here.
    # Add that to total_in once the payments table is joined.
    expected_now = float(s.opening_float or 0.0) + float(total_in) - float(total_out)

    return {
        "shift_id": s.id,
        "branch_id": s.branch_id,
        "opened_at": s.opened_at,
        "opening_float": s.opening_float,
        "locked": bool(s.locked),
        "expected_now": expected_now,
        "closed_at": s.closed_at,
        "expected_cash_final": s.expected_cash,
        "actual_cash_final": s.actual_cash,
        "close_note": s.close_note,
        "movements": [
            {
                "id": m.id,
                "kind": m.kind,
                "amount": float(m.amount),
                "reason": m.reason,
                "ts": m.created_at,
            }
            for m in mov_rows
        ],
    }


@router.post("/{shift_id}/payin")
def payin(
    shift_id: str,
    amount: float,
    reason: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Cash going INTO drawer (top-up, etc.)
    """
    s = db.get(Shift, shift_id)
    _assert_shift_ctx(ctx, s)
    if s.locked:
        raise HTTPException(400, detail="Shift is not open")

    m = CashMovement(
        shift_id=shift_id,
        kind="PAYIN",
        amount=amount,
        reason=reason,
    )
    db.add(m)
    db.commit()
    db.refresh(m)

    audit(
        db,
        ctx.user_id,
        "cash_movement",
        m.id,
        "PAYIN",
        after={"amount": amount, "reason": reason},
    )
    db.commit()

    return {"movement_id": m.id}


@router.post("/{shift_id}/payout")
def payout(
    shift_id: str,
    amount: float,
    reason: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Cash going OUT of drawer (petty cash, purchase, etc.)
    """
    s = db.get(Shift, shift_id)
    _assert_shift_ctx(ctx, s)
    if s.locked:
        raise HTTPException(400, detail="Shift is not open")

    m = CashMovement(
        shift_id=shift_id,
        kind="PAYOUT",
        amount=amount,
        reason=reason,
    )
    db.add(m)
    db.commit()
    db.refresh(m)

    audit(
        db,
        ctx.user_id,
        "cash_movement",
        m.id,
        "PAYOUT",
        after={"amount": amount, "reason": reason},
    )
    db.commit()

    return {"movement_id": m.id}


@router.post("/{shift_id}/close")
def close_shift(
    shift_id: str,
    expected_cash: float,
    actual_cash: float,
    note: str | None = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SHIFT_CLOSE")),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Close and lock the shift. If mismatch, caller also needs MANAGER_APPROVE.
    """
    s = db.get(Shift, shift_id)
    _assert_shift_ctx(ctx, s)
    if s.locked:
        raise HTTPException(409, detail="shift already closed")

    mismatch = float(actual_cash) - float(expected_cash)
    if mismatch != 0.0 and not _user_has_perm(db, sub, "MANAGER_APPROVE"):
        raise HTTPException(403, detail="Manager approval required for mismatch")

    s.expected_cash = expected_cash
    s.actual_cash = actual_cash
    s.close_note = note
    s.closed_by = sub
    s.closed_at = datetime.now(timezone.utc)
    s.locked = True
    db.commit()

    audit(
        db,
        sub,
        "shift",
        s.id,
        "CLOSE",
        after={
            "expected_cash": expected_cash,
            "actual_cash": actual_cash,
            "note": note,
            "mismatch": mismatch,
        },
    )
    db.commit()

    return {"ok": True, "mismatch": mismatch}
