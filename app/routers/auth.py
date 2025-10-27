from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

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
            "sub": str(user.id),
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "branch_id": str(default_branch_id) if default_branch_id else None,
        })
    except TypeError:
        # create_token likely expects just a subject string (legacy behavior)
        token = create_token(user.id)

    return Token(access_token=token)


@router.get("/me")
def me(
    sub: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """
    Return the logged-in user's profile + RBAC info.
    We ALSO include a default branch_id now,
    so the POS / settings UI knows which branch it's working on.
    """
    u: User | None = db.get(User, sub)
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

    # NEW PART: pick a "current" branch for this tenant.
    # For now, just grab the first branch belonging to this tenant.
    from app.models.core import Branch  # import here to avoid circulars
    branch = (
        db.query(Branch)
        .filter(Branch.tenant_id == u.tenant_id)
        .first()
    )
    branch_id = branch.id if branch else None

    return {
        "id": u.id,
        "tenant_id": u.tenant_id,
        "branch_id": branch_id,              # <-- NEW FIELD
        "name": u.name,
        "mobile": u.mobile,
        "email": u.email,
        "active": bool(u.active),
        "roles": role_codes,
        "permissions": sorted(list(perm_codes)),
    }
