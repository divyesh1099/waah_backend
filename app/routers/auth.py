# app/routers/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from uuid import UUID

from app.schemas.common import Token
from app.util.security import create_token, verify_pw
from app.models.core import (
    User, Role, UserRole, RolePermission, Permission,
)
from app.db import get_db
from app.deps import require_auth, AuthCtx  # <-- AuthCtx-based dep

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------- helpers ----------
def _coerce_user_pk(sub: str):
    """Coerce JWT sub into the actual PK type for Session.get()."""
    if sub is None:
        return None
    try:
        return int(sub)
    except (TypeError, ValueError):
        pass
    try:
        return UUID(str(sub))
    except (TypeError, ValueError):
        pass
    return str(sub)

def _s(v):
    """Safe stringify for JSON (ids, UUIDs, None)."""
    return "" if v is None else str(v)


@router.post("/login", response_model=Token)
def login(
    mobile: str | None = None,
    username: str | None = None,
    password: str | None = None,
    pin: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Auth by either:
    - mobile + password/pin
    - username + password/pin
    """
    if not mobile and not username:
        raise HTTPException(status_code=400, detail="Either mobile or username is required")

    user = None
    if username:
        user = db.query(User).filter(User.username == username).first()
    
    if not user and mobile:
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

    # Compute a sensible default branch for user's tenant
    from app.models.core import Branch
    default_branch = (
        db.query(Branch)
        .filter(Branch.tenant_id == user.tenant_id)
        .order_by(Branch.id.asc())
        .first()
    )
    default_branch_id = default_branch.id if default_branch else None

    # Emit both long/short claim names for compatibility (tenant_id/tid, branch_id/bid)
    claims = {
        "sub": _s(user.id),
        "tenant_id": _s(user.tenant_id) or None,
        "tid": _s(user.tenant_id) or None,
        "branch_id": _s(default_branch_id) or None,
        "bid": _s(default_branch_id) or None,
    }
    token = create_token(claims)
    return Token(access_token=token)


@router.get("/me")
def me(
    ctx: AuthCtx = Depends(require_auth),   # <-- IMPORTANT: use AuthCtx
    db: Session = Depends(get_db),
):
    """
    Return the logged-in user's profile + RBAC info.
    Uses user_id/tenant_id/branch_id from AuthCtx produced by require_auth.
    """
    # Load user by PK (ctx.user_id is normalized string)
    u: User | None = db.get(User, ctx.user_id)
    if not u or not bool(u.active):
        raise HTTPException(status_code=404, detail="user not found or inactive")

    # roles -> ["ADMIN", "CASHIER", ...]
    role_codes = [
        rc for (rc,) in (
            db.query(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .filter(UserRole.user_id == u.id)
            .all()
        )
    ]

    # permissions -> distinct set
    perm_codes = {
        pc for (pc,) in (
            db.query(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .filter(UserRole.user_id == u.id)
            .all()
        )
    }

    # Prefer branch from token (ctx.branch_id); fallback to first tenant branch
    branch_id = ctx.branch_id
    if not branch_id:
        from app.models.core import Branch
        b = db.query(Branch).filter(Branch.tenant_id == ctx.tenant_id).first()
        branch_id = b.id if b else None

    return {
        "id": str(u.id),
        "tenant_id": str(ctx.tenant_id) if ctx.tenant_id else None,
        "branch_id": str(branch_id) if branch_id else None,
        "name": u.name,
        "mobile": u.mobile,
        "email": u.email,
        "active": bool(u.active),
        "roles": role_codes,
        "permissions": sorted(list(perm_codes)),
    }
