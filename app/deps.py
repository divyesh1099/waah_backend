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
def _normalize_user_id(v) -> str:
    """Make sure we always have a scalar PK string for SQLAlchemy filters."""
    if isinstance(v, (list, tuple)) and v:
        return str(v[0])
    return "" if v is None else str(v)

def _fetch_user_tenant(db: Session, user_id: str) -> str:
    uid = _normalize_user_id(user_id)
    # Query just the needed column; avoid Session.get() pitfalls with odd IDs
    row = db.query(User.tenant_id).filter(User.id == uid).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    return row[0]

def _user_permissions(db: Session, user_id: str) -> set[str]:
    uid = _normalize_user_id(user_id)
    q = (
        db.query(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == uid)
    )
    return {row[0] for row in q.all()}

def _has_admin_role(db: Session, user_id: str) -> bool:
    uid = _normalize_user_id(user_id)
    return bool(
        db.query(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == uid, Role.code == "ADMIN")
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

    user_id_raw = data.get("sub") or data.get("uid")
    user_id = _normalize_user_id(user_id_raw)
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
    uid = _normalize_user_id(user_id)
    if _has_admin_role(db, uid):
        return True
    return code in _user_permissions(db, uid)


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
