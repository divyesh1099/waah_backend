from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db import get_db
from app.deps import require_perm
from app.util.security import hash_pw
from app.models.core import (
    Tenant,
    User,
    Role,
    UserRole,
    Permission,
    RolePermission,
)

router = APIRouter(prefix="/users", tags=["users"])


# ── Users ───────────────────────────────────────────────────────────────────

@router.post("/", summary="Create a new user (and optionally attach roles)")
def create_user(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    body:
    {
      "tenant_id": "...",          # optional, defaults to caller's tenant
      "name": "Alice",
      "mobile": "9999",
      "email": "alice@example.com",
      "password": "secret",        # optional, defaults to "admin"
      "pin": "1234",               # optional
      "roles": ["ADMIN","CASHIER"] # optional
    }
    """

    # 1) resolve tenant_id (fallback = caller's tenant)
    tid = (body.get("tenant_id") or "").strip()
    if not tid:
        me = db.get(User, sub)
        if not me:
            raise HTTPException(400, detail="tenant_id is required")
        tid = me.tenant_id

    # 2) tenant must exist
    if not db.get(Tenant, tid):
        raise HTTPException(400, detail=f"Invalid tenant_id: {tid}")

    # 3) check mobile uniqueness inside tenant
    mobile = body.get("mobile")
    if mobile:
        dup = (
            db.query(User)
            .filter(
                User.tenant_id == tid,
                User.mobile == mobile,
                User.deleted_at.is_(None),
            )
            .first()
        )
        if dup:
            raise HTTPException(409, detail="Mobile already exists")

    # 4) create the user
    u = User(
        tenant_id=tid,
        branch_id=body.get("branch_id"),
        name=body["name"],
        mobile=body.get("mobile"),
        email=body.get("email"),
        pass_hash=hash_pw(body.get("password", "admin")),
        active=True,
    )
    if body.get("pin"):
        u.pin_hash = hash_pw(body["pin"])

    db.add(u)
    db.flush()  # now u.id is available

    # 5) attach / create roles in same tenant
    for rcode in body.get("roles", []):
        r = (
            db.query(Role)
            .filter(Role.tenant_id == tid, Role.code == rcode)
            .first()
        )
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
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    [
      {
        "id": "...",
        "tenant_id": "...",
        "name": "...",
        "mobile": "...",
        "email": "...",
        "active": true,
        "roles": ["ADMIN","CASHIER"]
      },
      ...
    ]
    """

    q = db.query(User).filter(User.deleted_at.is_(None))
    if tenant_id:
        q = q.filter(User.tenant_id == tenant_id)
    
    eff_branch = ctx.branch_id or (branch_id or "").strip()
    if eff_branch:
        q = q.filter(User.branch_id == eff_branch)

    users = (
        q.order_by(User.created_at.desc())
        .limit(500)
        .all()
    )

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
            "branch_id": getattr(u, "branch_id", None),
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
    body: dict,  # { "roles": ["ADMIN","CASHIER"] }
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, detail="user not found")

    for code in body.get("roles", []):
        # role must share tenant with user
        r = (
            db.query(Role)
            .filter(Role.tenant_id == u.tenant_id, Role.code == code)
            .first()
        )
        if not r:
            r = Role(tenant_id=u.tenant_id, code=code)
            db.add(r)
            db.flush()

        if not (
            db.query(UserRole)
            .filter(UserRole.user_id == u.id, UserRole.role_id == r.id)
            .first()
        ):
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

    r = (
        db.query(Role)
        .filter(Role.tenant_id == u.tenant_id, Role.code == role_code)
        .first()
    )
    if not r:
        raise HTTPException(404, detail="role not found")

    db.query(UserRole).filter(
        UserRole.user_id == u.id,
        UserRole.role_id == r.id,
    ).delete()

    db.commit()
    return {"ok": True}


# ── Roles & Permissions ─────────────────────────────────────────────────────

@router.get("/roles", summary="List roles for a tenant (or all)")
def list_roles(
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Returns a lightweight view used in RolesPage list:
    [
      {"id": "...", "tenant_id": "...", "code": "ADMIN"},
      ...
    ]

    (Frontend's RoleInfo.fromListJson can default permissions=[] for these.)
    """
    q = db.query(Role)
    if tenant_id:
        q = q.filter(Role.tenant_id == tenant_id)

    rows = q.order_by(Role.code.asc()).all()

    return [
        {
            "id": r.id,
            "tenant_id": r.tenant_id,
            "code": r.code,
            # we intentionally don't join permissions here
            # to keep this list fast/light.
        }
        for r in rows
    ]


@router.get(
    "/roles/{role_id}",
    summary="Fetch single role including its permissions[]",
)
def get_role(
    role_id: str,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Returns full detail for RoleDetailPage:
    {
      "id": "...",
      "tenant_id": "...",
      "code": "CASHIER",
      "permissions": ["SETTINGS_EDIT", "REPRINT", ...]
    }
    """
    r = db.get(Role, role_id)
    if not r:
        raise HTTPException(404, detail="role not found")

    perm_rows = (
        db.query(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .filter(RolePermission.role_id == r.id)
        .all()
    )
    perms = [code for (code,) in perm_rows]

    return {
        "id": r.id,
        "tenant_id": r.tenant_id,
        "code": r.code,
        "permissions": perms,
    }


@router.post("/roles", summary="Create a role in a tenant")
def create_role(
    body: dict,  # { "tenant_id": "...", "code": "CASHIER" }
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    tid = body["tenant_id"]
    code = body["code"]

    # enforce uniqueness per tenant
    exists = (
        db.query(Role)
        .filter(Role.tenant_id == tid, Role.code == code)
        .first()
    )
    if exists:
        raise HTTPException(
            status_code=409,
            detail="role code already exists for this tenant",
        )

    r = Role(tenant_id=tid, code=code)
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"id": r.id}


@router.get("/permissions", summary="List all permissions")
def list_permissions(
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    [
      {"id": "...", "code": "SETTINGS_EDIT", "description": "..."},
      ...
    ]
    """
    rows = (
        db.query(Permission)
        .order_by(Permission.code.asc())
        .all()
    )
    return [
        {"id": p.id, "code": p.code, "description": p.description}
        for p in rows
    ]


@router.post(
    "/roles/{role_id}/grant",
    summary="Grant permissions to a role",
)
def grant_permissions(
    role_id: str,
    body: dict,  # { "permissions": ["SETTINGS_EDIT","REPRINT"] }
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    r = db.get(Role, role_id)
    if not r:
        raise HTTPException(404, detail="role not found")

    codes = set(body.get("permissions", []))
    if not codes:
        return {"id": r.id, "granted": 0}

    # preload Permission rows for those codes
    existing = {
        p.code: p
        for p in db.query(Permission)
        .filter(Permission.code.in_(list(codes)))
        .all()
    }

    # create any brand new Permission rows
    for c in codes - set(existing.keys()):
        p = Permission(code=c, description=c.title())
        db.add(p)
        db.flush()
        existing[c] = p

    # link role -> permission
    count = 0
    for c, p in existing.items():
        already = (
            db.query(RolePermission)
            .filter(
                RolePermission.role_id == r.id,
                RolePermission.permission_id == p.id,
            )
            .first()
        )
        if not already:
            db.add(
                RolePermission(
                    role_id=r.id,
                    permission_id=p.id,
                )
            )
            count += 1

    db.commit()
    return {"id": r.id, "granted": count}


@router.delete(
    "/roles/{role_id}/revoke/{perm_code}",
    summary="Revoke a permission from a role",
)
def revoke_permission(
    role_id: str,
    perm_code: str,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    r = db.get(Role, role_id)
    if not r:
        raise HTTPException(404, detail="role not found")

    p = (
        db.query(Permission)
        .filter(Permission.code == perm_code)
        .first()
    )
    if not p:
        raise HTTPException(404, detail="permission not found")

    db.query(RolePermission).filter(
        RolePermission.role_id == r.id,
        RolePermission.permission_id == p.id,
    ).delete()

    db.commit()
    return {"ok": True}
