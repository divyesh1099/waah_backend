# app/routers/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from uuid import UUID

from app.schemas.common import Token
from app.util.security import create_token, verify_pw, hash_pw
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



from pydantic import BaseModel
class LoginRequest(BaseModel):
    mobile: str | None = None
    username: str | None = None
    password: str | None = None
    pin: str | None = None

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class ChangePinRequest(BaseModel):
    current_pin: str
    new_pin: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class VerifyPasswordRequest(BaseModel):
    password: str

@router.post("/login", response_model=Token)
def login(
    creds: LoginRequest,
    db: Session = Depends(get_db),
):
    mobile = creds.mobile
    username = creds.username
    password = creds.password
    pin = creds.pin
    """
    Auth by either:
    - mobile + password/pin
    - username + password/pin
    """
    import sys
    import traceback
    try:
        # Auth by either...
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
    except Exception as e:
        with open("crash.log", "w") as f:
            print("CRASH IN LOGIN:", file=f)
            traceback.print_exc(file=f)
        raise e


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


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Authenticated password change for the current user.
    Validates current password, updates hash, and returns a fresh JWT.
    """
    u: User | None = db.get(User, ctx.user_id)
    if not u or not bool(u.active):
        raise HTTPException(status_code=404, detail="user not found")

    if not u.pass_hash or not verify_pw(u.pass_hash, body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    u.pass_hash = hash_pw(body.new_password)
    db.commit()

    # Re-issue token with same claims as login
    from app.models.core import Branch  # local import to avoid cycles
    default_branch = (
        db.query(Branch)
        .filter(Branch.tenant_id == u.tenant_id)
        .order_by(Branch.id.asc())
        .first()
    )
    default_branch_id = default_branch.id if default_branch else None

    claims = {
        "sub": _s(u.id),
        "tenant_id": _s(u.tenant_id) or None,
        "tid": _s(u.tenant_id) or None,
        "branch_id": _s(default_branch_id) or None,
        "bid": _s(default_branch_id) or None,
    }
    token = create_token(claims)
    return {"ok": True, "access_token": token}


@router.post("/change-pin")
def change_pin(
    body: ChangePinRequest,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Authenticated PIN change for the current user.
    Validates current PIN, updates hash.
    """
    u: User | None = db.get(User, ctx.user_id)
    if not u or not bool(u.active):
        raise HTTPException(status_code=404, detail="user not found")

    if not u.pin_hash or not verify_pw(u.pin_hash, body.current_pin):
        raise HTTPException(status_code=400, detail="Current PIN is incorrect")

    if len(body.new_pin) < 4:
        raise HTTPException(status_code=400, detail="New PIN must be at least 4 digits")

    u.pin_hash = hash_pw(body.new_pin)
    db.commit()
    return {"ok": True}


@router.post("/verify-password")
def verify_password(
    body: VerifyPasswordRequest,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Check that the supplied password matches the current user.
    Used by clients to gate dangerous operations.
    """
    u: User | None = db.get(User, ctx.user_id)
    if not u or not bool(u.active):
        raise HTTPException(status_code=404, detail="user not found or inactive")
    if not verify_pw(u.pass_hash, body.password):
        raise HTTPException(status_code=400, detail="Invalid password")
    return {"ok": True}
