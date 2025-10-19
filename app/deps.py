from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from sqlalchemy.orm import Session
from app.config import settings
from app.db import get_db
from app.models.core import User, Role, RolePermission, Permission, UserRole

auth_scheme = HTTPBearer(auto_error=False)

def require_db(db=Depends(get_db)):
    return db

def require_auth(creds: HTTPAuthorizationCredentials | None = Depends(auth_scheme)) -> str:
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        data = jwt.decode(creds.credentials, settings.APP_SECRET, algorithms=["HS256"], options={"verify_aud": False})
        return data["sub"]
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

def _user_permissions(db: Session, user_id: str) -> set[str]:
    q = (db.query(Permission.code)
         .join(RolePermission, RolePermission.permission_id == Permission.id)
         .join(Role, Role.id == RolePermission.role_id)
         .join(UserRole, UserRole.role_id == Role.id)
         .filter(UserRole.user_id == user_id))
    return {row[0] for row in q.all()}

def has_perm(db: Session, user_id: str, code: str) -> bool:
    return code in _user_permissions(db, user_id)

def require_perm(code: str):
    def _dep(sub: str = Depends(require_auth), db: Session = Depends(get_db)):
        # Admin shortcut: user has a role named ADMIN → allow
        has_admin = (
            db.query(Role)
              .join(UserRole, UserRole.role_id == Role.id)
              .filter(UserRole.user_id == sub, Role.code == "ADMIN")
              .first()
        )
        if has_admin:
            return sub

        # Gather user’s permissions via (UserRole → Role → RolePermission → Permission)
        q = (
            db.query(Permission.code)
              .join(RolePermission, RolePermission.permission_id == Permission.id)
              .join(Role, Role.id == RolePermission.role_id)
              .join(UserRole, UserRole.role_id == Role.id)
              .filter(UserRole.user_id == sub)
        )
        user_perms = {row[0] for row in q.all()}

        if code not in user_perms:
            raise HTTPException(status_code=403, detail=f"Missing permission: {code}")
        return sub
    return _dep
