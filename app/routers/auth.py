from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.schemas.common import Token
from app.util.security import create_token, verify_pw
from app.models.core import (
    User,
    Role,
    UserRole,
    RolePermission,
    Permission,
)
from app.db import get_db
from app.deps import require_auth

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- helpers ----------
def _coerce_user_pk(sub: str):
    """Coerce JWT sub into the actual PK type for Session.get()."""
    if sub is None:
        return None
    # try int
    try:
        return int(sub)
    except (TypeError, ValueError):
        pass
    # try UUID
    try:
        return UUID(str(sub))
    except (TypeError, ValueError):
        pass
    # fallback: string PKs
    return str(sub)

def _s(v):
    """Safe stringify for JSON (ids, UUIDs, None)."""
    return "" if v is None else str(v)


@router.post("/login", response_model=Token)
def login(
    mobile: str,
    password: str | None = None,
    pin: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Auth by either:
    - mobile + password
    - mobile + pin
    """
    user = db.query(User).filter(User.mobile == mobile).first()
    if not user or not bool(user.active):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    ok = False
    if password:
        ok = verify_pw(user.pass_hash, password)
    if not ok and pin and user.pin_hash:
        ok = verify_pw(user.pin_hash, pin)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Compute a sensible default branch for the user’s tenant (same logic as /me)
    from app.models.core import Branch
    default_branch = (
        db.query(Branch)
        .filter(Branch.tenant_id == user.tenant_id)
        .first()
    )
    default_branch_id = default_branch.id if default_branch else None

    # Prefer tokens that carry tenant/branch claims; fall back to legacy signature
    try:
        token = create_token({
            "sub": _s(user.id),
            "tenant_id": _s(user.tenant_id) or None,
            "branch_id": _s(default_branch_id) or None,
        })
    except TypeError:
        # create_token likely expects just a subject string (legacy behavior)
        token = create_token(_s(user.id))

    return Token(access_token=token)


@router.get("/me")
def me(
    sub: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """
    Return the logged-in user's profile + RBAC info.
    Includes a default branch_id for the tenant.
    Critical fix: coerce JWT `sub` to the correct PK type before Session.get().
    """
    pk = _coerce_user_pk(sub)
    if pk is None:
        raise HTTPException(status_code=401, detail="Invalid token (no sub)")

    u: User | None = db.get(User, pk)
    if not u or not bool(u.active):
        raise HTTPException(status_code=404, detail="user not found or inactive")

    # roles -> ["ADMIN", "CASHIER", ...]
    role_codes = [
        rc
        for (rc,) in (
            db.query(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .filter(UserRole.user_id == u.id)
            .all()
        )
    ]

    # permissions -> distinct ["SETTINGS_EDIT", ...]
    perm_codes = {
        pc
        for (pc,) in (
            db.query(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .filter(UserRole.user_id == u.id)
            .all()
        )
    }

    # Pick a "current" branch for this tenant.
    # Prefer a user-level active branch if present; else first branch for tenant.
    from app.models.core import Branch  # local import to avoid circulars
    preferred_branch_id = getattr(u, "active_branch_id", None)
    branch_id = preferred_branch_id
    if not branch_id:
        branch = (
            db.query(Branch)
            .filter(Branch.tenant_id == u.tenant_id)
            .first()
        )
        branch_id = branch.id if branch else None

    return {
        "id": _s(u.id),
        "tenant_id": _s(u.tenant_id),
        "branch_id": _s(branch_id),          # frontend reads branch_id/branchId
        "name": u.name,
        "mobile": u.mobile,
        "email": u.email,
        "active": bool(u.active),
        "roles": role_codes,
        "permissions": sorted(list(perm_codes)),
    }
