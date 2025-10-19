# app/routers/users.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db import get_db
from app.deps import require_perm
from app.util.security import hash_pw
from app.models.core import Tenant, User, Role, UserRole, Permission, RolePermission

router = APIRouter(prefix="/users", tags=["users"])


# ── Users ───────────────────────────────────────────────────────────────────

@router.post("/")
def create_user(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    """
    body: {
      tenant_id?,                # optional: will default to caller's tenant
      name, mobile?, email?, password?, pin?,
      roles?: [code,...]
    }
    """
    # 1) Resolve tenant_id
    tid = (body.get("tenant_id") or "").strip()
    if not tid:
        # default to the creator's tenant
        me = db.get(User, sub)
        if not me:
            raise HTTPException(400, detail="tenant_id is required")
        tid = me.tenant_id

    # 2) Validate tenant exists
    if not db.get(Tenant, tid):
        raise HTTPException(400, detail=f"Invalid tenant_id: {tid}")

    # 3) Optional: basic uniqueness check within tenant
    if body.get("mobile"):
        exists = db.query(User).filter(User.tenant_id == tid, User.mobile == body["mobile"]).first()
        if exists:
            raise HTTPException(409, detail="Mobile already exists")

    # 4) Create user
    u = User(
        tenant_id=tid,
        name=body["name"],
        mobile=body.get("mobile"),
        email=body.get("email"),
        pass_hash=hash_pw(body.get("password", "admin")),
    )
    if body.get("pin"):
        u.pin_hash = hash_pw(body["pin"])
    db.add(u)
    db.flush()

    # 5) Attach roles (scoped to same tenant)
    for rcode in body.get("roles", []):
        r = db.query(Role).filter(Role.tenant_id == tid, Role.code == rcode).first()
        if not r:
            r = Role(tenant_id=tid, code=rcode)
            db.add(r)
            db.flush()
        db.add(UserRole(user_id=u.id, role_id=r.id))

    db.commit()
    db.refresh(u)
    return {"id": u.id}

@router.get("/", summary="List users (basic info + roles)")
def list_users(
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    q = db.query(User).filter(User.deleted_at.is_(None))
    if tenant_id:
        q = q.filter(User.tenant_id == tenant_id)
    users = q.order_by(User.created_at.desc()).limit(500).all()

    def _roles_for(uid: str) -> List[str]:
        rows = (
            db.query(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .filter(UserRole.user_id == uid)
            .all()
        )
        return [r[0] for r in rows]

    return [
        {
            "id": u.id,
            "tenant_id": u.tenant_id,
            "name": u.name,
            "mobile": u.mobile,
            "email": u.email,
            "active": bool(u.active),
            "roles": _roles_for(u.id),
        }
        for u in users
    ]


@router.post("/{user_id}/roles", summary="Assign roles to a user")
def assign_roles(
    user_id: str,
    body: dict,  # {roles: [code, ...]}
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, detail="user not found")

    for code in body.get("roles", []):
        r = db.query(Role).filter(Role.code == code).first()
        if not r:
            r = Role(tenant_id=u.tenant_id, code=code)
            db.add(r)
            db.flush()
        if not db.query(UserRole).filter(UserRole.user_id == u.id, UserRole.role_id == r.id).first():
            db.add(UserRole(user_id=u.id, role_id=r.id))
    db.commit()
    return {"id": u.id}


@router.delete("/{user_id}/roles/{role_code}", summary="Remove a role from a user")
def remove_role(
    user_id: str,
    role_code: str,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, detail="user not found")
    r = db.query(Role).filter(Role.code == role_code).first()
    if not r:
        raise HTTPException(404, detail="role not found")
    db.query(UserRole).filter(UserRole.user_id == u.id, UserRole.role_id == r.id).delete()
    db.commit()
    return {"ok": True}


# ── Roles & Permissions ─────────────────────────────────────────────────────

@router.get("/roles", summary="List roles")
def list_roles(
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    q = db.query(Role)
    if tenant_id:
        q = q.filter(Role.tenant_id == tenant_id)
    rows = q.order_by(Role.code.asc()).all()
    return [{"id": r.id, "tenant_id": r.tenant_id, "code": r.code} for r in rows]


@router.post("/roles", summary="Create a role")
def create_role(
    body: dict,  # {tenant_id, code}
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    if db.query(Role).filter(Role.code == body["code"]).first():
        raise HTTPException(409, detail="role code already exists")
    r = Role(tenant_id=body["tenant_id"], code=body["code"])
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"id": r.id}


@router.get("/permissions", summary="List all permissions")
def list_permissions(
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    rows = db.query(Permission).order_by(Permission.code.asc()).all()
    return [{"id": p.id, "code": p.code, "description": p.description} for p in rows]


@router.post("/roles/{role_id}/grant", summary="Grant permissions to a role")
def grant_permissions(
    role_id: str,
    body: dict,  # {permissions: [code, ...]}
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    r = db.get(Role, role_id)
    if not r:
        raise HTTPException(404, detail="role not found")

    # map codes -> Permission, create if missing
    codes = set(body.get("permissions", []))
    if not codes:
        return {"id": r.id, "granted": 0}
    existing = {p.code: p for p in db.query(Permission).filter(Permission.code.in_(list(codes))).all()}
    # create missing permissions on the fly
    for c in codes - set(existing.keys()):
        p = Permission(code=c, description=c.title())
        db.add(p)
        db.flush()
        existing[c] = p

    # attach role-perms
    count = 0
    for c, p in existing.items():
        if not db.query(RolePermission).filter(RolePermission.role_id == r.id, RolePermission.permission_id == p.id).first():
            db.add(RolePermission(role_id=r.id, permission_id=p.id))
            count += 1
    db.commit()
    return {"id": r.id, "granted": count}


@router.delete("/roles/{role_id}/revoke/{perm_code}", summary="Revoke a permission from a role")
def revoke_permission(
    role_id: str,
    perm_code: str,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    r = db.get(Role, role_id)
    if not r:
        raise HTTPException(404, detail="role not found")
    p = db.query(Permission).filter(Permission.code == perm_code).first()
    if not p:
        raise HTTPException(404, detail="permission not found")
    db.query(RolePermission).filter(RolePermission.role_id == r.id, RolePermission.permission_id == p.id).delete()
    db.commit()
    return {"ok": True}
