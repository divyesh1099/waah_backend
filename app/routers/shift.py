from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from app.db import get_db
from app.deps import require_auth, has_perm, require_perm
from app.models.core import Shift, CashMovement
from app.util.audit import log_audit

router = APIRouter(prefix="/shift", tags=["shift"])

@router.post("/open")
def open_shift(branch_id: str, opening_float: float = 0.0, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    s = Shift(branch_id=branch_id, opened_by=sub, opened_at=datetime.now(timezone.utc), opening_float=opening_float)
    db.add(s); db.commit(); db.refresh(s)
    log_audit(db, sub, "shift", s.id, "OPEN", after={"opening_float": opening_float})
    db.commit()
    return {"shift_id": s.id}

@router.post("/{shift_id}/payin")
def payin(shift_id: str, amount: float, reason: str | None = None, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    m = CashMovement(shift_id=shift_id, kind="PAYIN", amount=amount, reason=reason)
    db.add(m); db.commit(); db.refresh(m)
    log_audit(db, sub, "cash_movement", m.id, "PAYIN", after={"amount": amount, "reason": reason})
    db.commit()
    return {"movement_id": m.id}

@router.post("/{shift_id}/payout")
def payout(shift_id: str, amount: float, reason: str | None = None, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    m = CashMovement(shift_id=shift_id, kind="PAYOUT", amount=amount, reason=reason)
    db.add(m); db.commit(); db.refresh(m)
    log_audit(db, sub, "cash_movement", m.id, "PAYOUT", after={"amount": amount, "reason": reason})
    db.commit()
    return {"movement_id": m.id}

@router.post("/{shift_id}/close")
def close_shift(shift_id: str, expected_cash: float, actual_cash: float, note: str | None = None,
                db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    s = db.get(Shift, shift_id)
    if not s:
        raise HTTPException(404, detail="shift not found")
    mismatch = float(actual_cash) - float(expected_cash)
    # require MANAGER_APPROVE when mismatch != 0
    if mismatch != 0.0 and not has_perm(db, sub, "MANAGER_APPROVE"):
        raise HTTPException(403, detail="Manager approval required for mismatch")
    s.expected_cash = expected_cash
    s.actual_cash = actual_cash
    s.close_note = note
    s.closed_by = sub
    s.closed_at = datetime.now(timezone.utc)
    s.locked = True
    log_audit(db, sub, "shift", s.id, "CLOSE", after={"expected": expected_cash, "actual": actual_cash, "note": note})
    db.commit()
    return {"ok": True, "mismatch": mismatch}


def has_perm(db: Session, user_id: str, code: str) -> bool:
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

@router.post("/{shift_id}/close")
def close_shift(shift_id: str, expected_cash: float, actual_cash: float, note: str | None = None,
                db: Session = Depends(get_db), sub: str = Depends(require_perm("SHIFT_CLOSE"))):
    s = db.get(Shift, shift_id)
    if not s:
        raise HTTPException(404, detail="shift not found")
    mismatch = float(actual_cash) - float(expected_cash)
    if mismatch != 0.0 and not has_perm(db, sub, "MANAGER_APPROVE"):
        raise HTTPException(403, detail="Manager approval required for mismatch")
    s.expected_cash = expected_cash
    s.actual_cash = actual_cash
    s.close_note = note
    s.closed_by = sub
    s.closed_at = datetime.now(timezone.utc)
    s.locked = True
    db.commit()
    return {"ok": True, "mismatch": mismatch}