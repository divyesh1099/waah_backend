from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Set

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.core import User, Role, RolePermission, Permission, UserRole

auth_scheme = HTTPBearer(auto_error=False)

# =========================
# Auth context
# =========================
@dataclass
class AuthCtx:
    user_id: str
    tenant_id: str
    branch_id: Optional[str]
    permissions: Set[str]


# =========================
# Small helpers
# =========================
def _as_str(v) -> Optional[str]:
    if v is None:
        return None
    # handle (value,) rows or single-item lists just in case
    if isinstance(v, (list, tuple)) and v:
        return str(v[0])
    return str(v)

def _normalize_pk(v) -> str:
    s = _as_str(v)
    return "" if s is None else s

def _get_user_row(db: Session, user_id: str) -> tuple[Optional[str], bool]:
    """
    Return (tenant_id, active_bool) for the user or (None, False) if missing.
    """
    uid = _normalize_pk(user_id)
    row = db.query(User.tenant_id, User.active).filter(User.id == uid).first()
    if not row:
        return None, False
    tenant_id, active = row[0], bool(row[1])
    return _as_str(tenant_id), active

def _user_permissions(db: Session, user_id: str) -> set[str]:
    uid = _normalize_pk(user_id)
    q = (
        db.query(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == uid)
    )
    return {code for (code,) in q.all()}

def _has_admin_role(db: Session, user_id: str) -> bool:
    uid = _normalize_pk(user_id)
    return bool(
        db.query(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == uid, Role.code == "ADMIN")
        .first()
    )


# =========================
# Public dependencies
# =========================
def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
    db: Session = Depends(get_db),
) -> AuthCtx:
    """
    Decode JWT and return a rich AuthCtx:
      - user_id (sub)
      - tenant_id (prefer token 'tenant_id'/'tid', else DB)
      - branch_id (optional; token 'branch_id'/'bid')
      - permissions (from DB)
    Also 401s on unknown/inactive users to prevent later 404s.
    """
    if not creds or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = creds.credentials
    try:
        data = jwt.decode(
            token,
            settings.APP_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},  # we don't use 'aud'
        )
    except ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user_id = _normalize_pk(data.get("sub") or data.get("uid"))
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token (no sub)")

    # Resolve tenant: prefer token, fallback to DB
    tenant_id = _as_str(data.get("tenant_id") or data.get("tid"))

    # Early user existence/active check (prevents /auth/me -> 404)
    db_tenant_id, is_active = _get_user_row(db, user_id)
    if not is_active:
        # Unknown or inactive user is treated as unauthorized
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or not found")

    if not tenant_id:
        if not db_tenant_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tenant not found for user")
        tenant_id = db_tenant_id

    # Branch is optional, pass through if present
    branch_id = _as_str(data.get("branch_id") or data.get("bid"))

    perms = _user_permissions(db, user_id)

    return AuthCtx(
        user_id=user_id,
        tenant_id=tenant_id,
        branch_id=branch_id,
        permissions=perms,
    )


def has_perm(db: Session, user_id: str, code: str) -> bool:
    uid = _normalize_pk(user_id)
    if _has_admin_role(db, uid):
        return True
    return code in _user_permissions(db, uid)


def require_perm(code: str):
    """
    RBAC dependency:
      - ADMIN role bypasses all checks.
      - Otherwise, 'code' must be in the user's permissions.
    Returns AuthCtx for downstream handlers.
    """
    def _dep(ctx: AuthCtx = Depends(require_auth), db: Session = Depends(get_db)) -> AuthCtx:
        if _has_admin_role(db, ctx.user_id):
            return ctx
        if code not in ctx.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {code}")
        return ctx
    return _dep
