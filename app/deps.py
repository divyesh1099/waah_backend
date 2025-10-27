from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Set

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.core import (
    User, Role, RolePermission, Permission, UserRole
)

auth_scheme = HTTPBearer(auto_error=False)


# ---------------------------
# Auth context returned by require_auth
# ---------------------------
@dataclass
class AuthCtx:
    user_id: str
    tenant_id: str
    # May be None if the client hasn't selected a branch yet
    branch_id: Optional[str]
    permissions: Set[str]


def require_db(db=Depends(get_db)):
    return db


# ---------------------------
# Internal helpers
# ---------------------------
def _fetch_user_tenant(db: Session, user_id: str) -> str:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    return u.tenant_id

def _user_permissions(db: Session, user_id: str) -> set[str]:
    q = (
        db.query(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == user_id)
    )
    return {row[0] for row in q.all()}

def _has_admin_role(db: Session, user_id: str) -> bool:
    return bool(
        db.query(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == user_id, Role.code == "ADMIN")
        .first()
    )


# ---------------------------
# Public dependencies
# ---------------------------
def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
    db: Session = Depends(get_db),
) -> AuthCtx:
    """
    Decode JWT and return a rich AuthCtx:
      - user_id (sub)
      - tenant_id (from token 'tid'/'tenant_id' or DB fallback)
      - branch_id (from token 'bid'/'branch_id' if present)
      - permissions (computed from DB)
    """
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        data = jwt.decode(
            creds.credentials,
            settings.APP_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user_id = data.get("sub") or data.get("uid")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token (no sub)")

    # Prefer tenant from token, fallback to DB lookup from user row
    tenant_id = data.get("tid") or data.get("tenant_id")
    if not tenant_id:
        tenant_id = _fetch_user_tenant(db, user_id)

    # Branch is optional in token; if missing, routes can decide how to proceed
    branch_id = data.get("bid") or data.get("branch_id")

    perms = _user_permissions(db, user_id)

    return AuthCtx(
        user_id=user_id,
        tenant_id=tenant_id,
        branch_id=branch_id,
        permissions=perms,
    )


def has_perm(db: Session, user_id: str, code: str) -> bool:
    # Keep compatibility helper
    if _has_admin_role(db, user_id):
        return True
    return code in _user_permissions(db, user_id)


def require_perm(code: str):
    """
    RBAC dependency:
      - ADMIN role bypasses all checks.
      - Otherwise, the permission must be granted.
    Returns AuthCtx (same as require_auth) for convenience.
    """
    def _dep(ctx: AuthCtx = Depends(require_auth), db: Session = Depends(get_db)) -> AuthCtx:
        if _has_admin_role(db, ctx.user_id):
            return ctx
        if code not in ctx.permissions:
            raise HTTPException(status_code=403, detail=f"Missing permission: {code}")
        return ctx
    return _dep
