from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime

from app.db import get_db
from app.config import settings
from app.util.security import hash_pw
from app.models.core import (
    Tenant, Branch, User, RestaurantSettings,
    Printer, PrinterType, KitchenStation,
    Role, Permission, RolePermission, UserRole,
    OnboardProgress
)

router = APIRouter(prefix="/onboard", tags=["onboard"])

# ── helpers ─────────────────────────────────────────────────────────────────

def _require_setup_secret(request: Request):
    # allow in dev; otherwise require the shared secret for first-run wizard
    if settings.APP_ENV == "dev":
        return
    secret = request.headers.get("X-App-Secret") or request.query_params.get("secret")
    if not secret or secret != settings.APP_SECRET:
        raise HTTPException(403, detail="Not allowed")

def _ensure_admin_role_and_perms(db: Session, tenant_id: str) -> str:
    admin_role = db.query(Role).filter(Role.tenant_id == tenant_id, Role.code == "ADMIN").first()
    if not admin_role:
        admin_role = Role(tenant_id=tenant_id, code="ADMIN")
        db.add(admin_role); db.flush()
    needed = ["SETTINGS_EDIT", "REPRINT", "VOID", "DISCOUNT", "MANAGER_APPROVE", "SHIFT_CLOSE"]
    existing = {p.code: p for p in db.query(Permission).filter(Permission.code.in_(needed)).all()}
    for code in needed:
        perm = existing.get(code)
        if not perm:
            perm = Permission(code=code, description=None)
            db.add(perm); db.flush()
        if not db.query(RolePermission).filter_by(role_id=admin_role.id, permission_id=perm.id).first():
            db.add(RolePermission(role_id=admin_role.id, permission_id=perm.id))
    return admin_role.id

def _progress(db: Session, tenant_id: str, step: str | None = None, note: str | None = None, completed: bool | None = None):
    pg = db.query(OnboardProgress).filter(OnboardProgress.tenant_id == tenant_id).first()
    if not pg:
        pg = OnboardProgress(tenant_id=tenant_id, step=step or "ADMIN", last_note=note)
        db.add(pg)
    else:
        if step: pg.step = step
        if note is not None: pg.last_note = note
        if completed is not None: pg.completed = completed
    db.commit()

# ── endpoints ───────────────────────────────────────────────────────────────

@router.get("/status")
def status(tenant_id: str | None = None, db: Session = Depends(get_db)):
    # If tenant_id provided, check its setup; else report global empty-state
    if tenant_id:
        t = db.get(Tenant, tenant_id)
        if not t:
            raise HTTPException(404, detail="tenant not found")

        branch_count = db.query(func.count(Branch.id)).filter(Branch.tenant_id == tenant_id).scalar() or 0
        rs_count = db.query(func.count(RestaurantSettings.id)).filter(RestaurantSettings.tenant_id == tenant_id).scalar() or 0
        bill_printers = (
            db.query(func.count(Printer.id))
            .filter(Printer.tenant_id == tenant_id, Printer.type == PrinterType.BILLING)
            .scalar() or 0
        )
        stations = db.query(func.count(KitchenStation.id)).join(Printer, Printer.id == KitchenStation.printer_id, isouter=True)\
                    .filter(KitchenStation.tenant_id == tenant_id).scalar() or 0
        pg = db.query(OnboardProgress).filter(OnboardProgress.tenant_id == tenant_id).first()

        missing = []
        if branch_count == 0: missing.append("BRANCH")
        if rs_count == 0: missing.append("SETTINGS")
        if bill_printers == 0: missing.append("BILLING_PRINTER")
        if stations == 0: missing.append("STATIONS")

        return {
            "tenant_id": tenant_id,
            "completed": pg.completed if pg else False,
            "current_step": (pg.step if pg else ("ADMIN" if not missing else missing[0])),
            "missing": missing,
            "counts": {
                "branches": branch_count, "restaurant_settings": rs_count,
                "billing_printers": bill_printers, "stations": stations,
            }
        }

    # no tenant yet?
    any_tenant = db.query(func.count(Tenant.id)).scalar() or 0
    return {"system_initialized": any_tenant > 0}

@router.post("/admin")
def create_tenant_and_admin(
    body: dict, request: Request, db: Session = Depends(get_db)
):
    """
    First screen: create Tenant + first Admin user.
    Requires X-App-Secret in prod (or dev env).
    Body: { tenant_name, admin_name, mobile, email, password, pin? }
    """
    _require_setup_secret(request)

    for field in ("tenant_name", "admin_name", "mobile", "password"):
        if not body.get(field):
            raise HTTPException(400, detail=f"missing field: {field}")

    # Create tenant
    t = Tenant(name=body["tenant_name"])
    db.add(t); db.flush()

    # Admin user
    u = User(
        tenant_id=t.id,
        name=body["admin_name"],
        mobile=body["mobile"],
        email=body.get("email"),
        pass_hash=hash_pw(body["password"]),
        pin_hash=hash_pw(body["pin"]) if body.get("pin") else None,
        active=True,
    )
    db.add(u); db.flush()

    # RBAC
    admin_role_id = _ensure_admin_role_and_perms(db, t.id)
    if not db.query(UserRole).filter_by(user_id=u.id, role_id=admin_role_id).first():
        db.add(UserRole(user_id=u.id, role_id=admin_role_id))

    db.commit()
    _progress(db, t.id, step="BRANCH", note="Tenant & admin created", completed=False)
    return {"tenant_id": t.id, "admin_user_id": u.id, "next": "BRANCH"}

@router.post("/branch")
def create_branch(
    body: dict, request: Request, db: Session = Depends(get_db)
):
    """
    Adds first Branch for a tenant.
    Body: { tenant_id, name, phone?, gstin?, address?, state_code? }
    """
    _require_setup_secret(request)
    tenant_id = body.get("tenant_id")
    if not tenant_id: raise HTTPException(400, detail="missing tenant_id")
    if not db.get(Tenant, tenant_id): raise HTTPException(404, detail="tenant not found")
    if not body.get("name"): raise HTTPException(400, detail="missing name")

    b = Branch(
        tenant_id=tenant_id,
        name=body["name"],
        phone=body.get("phone"),
        state_code=body.get("state_code"),
        gstin=body.get("gstin"),
        address=body.get("address"),
    )
    db.add(b); db.flush()
    db.commit()
    _progress(db, tenant_id, step="SETTINGS", note="Branch created", completed=False)
    return {"branch_id": b.id, "next": "SETTINGS"}

@router.post("/restaurant")
def upsert_branch_settings(
    body: dict, request: Request, db: Session = Depends(get_db)
):
    """
    Branch legal/profile settings.
    Body: RestaurantSettings fields (tenant_id, branch_id, name, address, phone, gstin, fssai, etc.)
    """
    _require_setup_secret(request)
    for k in ("tenant_id", "branch_id", "name"):
        if not body.get(k): raise HTTPException(400, detail=f"missing {k}")

    rs = (db.query(RestaurantSettings)
            .filter(RestaurantSettings.tenant_id==body["tenant_id"],
                    RestaurantSettings.branch_id==body["branch_id"]).first())
    if not rs:
        rs = RestaurantSettings(**body)
        db.add(rs)
    else:
        for k, v in body.items():
            setattr(rs, k, v)
    db.commit(); db.refresh(rs)
    _progress(db, body["tenant_id"], step="PRINTERS", note="Restaurant settings saved", completed=False)
    return {"restaurant_settings_id": rs.id, "next": "PRINTERS"}

@router.post("/printers")
def setup_printers_and_stations(
    body: dict, request: Request, db: Session = Depends(get_db)
):
    """
    Creates billing printer, kitchen printers, and stations.
    Body:
    {
      "tenant_id": "...", "branch_id": "...",
      "billing": { "name":"Billing", "connection_url":"http://agent:9100", "is_default": true, "cash_drawer_enabled": true, "cash_drawer_code":"PULSE_2_100" },
      "kitchen": [
         { "name":"Kitchen-1", "connection_url":"http://agent:9101", "is_default": true, "stations":["Indian","Chinese"] },
         { "name":"Tandoor-PRN", "connection_url":"http://agent:9102", "stations":["Tandoor"] }
      ]
    }
    """
    _require_setup_secret(request)
    tenant_id, branch_id = body.get("tenant_id"), body.get("branch_id")
    if not tenant_id or not branch_id:
        raise HTTPException(400, detail="missing tenant_id/branch_id")

    # Billing
    if "billing" in body and body["billing"]:
        b = dict(body["billing"])
        p = Printer(
            tenant_id=tenant_id,
            branch_id=branch_id,
            name=b.get("name","Billing Printer"),
            type=PrinterType.BILLING,
            connection_url=b.get("connection_url"),
            is_default=bool(b.get("is_default", True)),
            cash_drawer_enabled=bool(b.get("cash_drawer_enabled", False)),
            cash_drawer_code=b.get("cash_drawer_code"),
        )
        db.add(p); db.flush()
        # bind to settings if present
        rs = (db.query(RestaurantSettings)
              .filter(RestaurantSettings.tenant_id==tenant_id,
                      RestaurantSettings.branch_id==branch_id).first())
        if rs:
            rs.billing_printer_id = p.id

    # Kitchens
    created = {"kitchen_printers": [], "stations": []}
    for kdef in body.get("kitchen", []) or []:
        kp = Printer(
            tenant_id=tenant_id,
            branch_id=branch_id,
            name=kdef.get("name","Kitchen Printer"),
            type=PrinterType.KITCHEN,
            connection_url=kdef.get("connection_url"),
            is_default=bool(kdef.get("is_default", False)),
        )
        db.add(kp); db.flush()
        created["kitchen_printers"].append(kp.id)
        for sname in kdef.get("stations", []) or []:
            st = KitchenStation(tenant_id=tenant_id, branch_id=branch_id, name=sname, printer_id=kp.id)
            db.add(st); db.flush()
            created["stations"].append(st.id)

    db.commit()
    _progress(db, tenant_id, step="FINISH", note="Printers & stations done", completed=False)
    return {"created": created, "next": "FINISH"}

@router.post("/finish")
def finish(body: dict, request: Request, db: Session = Depends(get_db)):
    """
    Marks onboarding as complete for a tenant.
    Body: { tenant_id }
    """
    _require_setup_secret(request)
    tenant_id = body.get("tenant_id")
    if not tenant_id: raise HTTPException(400, detail="missing tenant_id")
    if not db.get(Tenant, tenant_id): raise HTTPException(404, detail="tenant not found")
    _progress(db, tenant_id, step="FINISH", note="Onboarding complete", completed=True)
    return {"tenant_id": tenant_id, "completed": True}
