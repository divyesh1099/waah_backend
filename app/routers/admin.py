from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import settings
from app.util.security import hash_pw
from app.models.core import (
    Tenant, Branch, User,
    RestaurantSettings, Printer, PrinterType, KitchenStation,
    Role, Permission, RolePermission, UserRole,
)

router = APIRouter(prefix="/admin", tags=["admin"])

@router.post("/dev-bootstrap")
def dev_bootstrap(db: Session = Depends(get_db)):
    if settings.APP_ENV != "dev":
        raise HTTPException(403, detail="Not allowed")

    # Tenant
    t = db.query(Tenant).first()
    if not t:
        t = Tenant(name="Demo Tenant")
        db.add(t); db.flush()

    # Branch (now has phone/state_code/gstin in schema)
    b = db.query(Branch).first()
    if not b:
        b = Branch(
            tenant_id=t.id,
            name="Main Branch",
            phone="1800123456",
            state_code="MH",
            gstin="27ABCDE1234F2Z5",
            address="123 Food Street, Mumbai",
        )
        db.add(b); db.flush()

    # Admin user (with optional PIN)
    u = db.query(User).first()
    if not u:
        u = User(
            tenant_id=t.id,
            name="Admin",
            mobile="9999999999",
            email="admin@example.com",
            pass_hash=hash_pw("admin"),
            pin_hash=hash_pw("1234"),
            active=True,
        )
        db.add(u); db.flush()

    # Default printers (billing + kitchen)
    billing_pr = (
        db.query(Printer)
        .filter(Printer.branch_id == b.id, Printer.type == PrinterType.BILLING)
        .first()
    )
    if not billing_pr:
        billing_pr = Printer(
            tenant_id=t.id,
            branch_id=b.id,
            name="Billing Printer",
            type=PrinterType.BILLING,
            is_default=True,
            connection_url="http://localhost:9100/agent",  # adjust for your agent
            cash_drawer_enabled=True,
            cash_drawer_code="PULSE_2_100",
        )
        db.add(billing_pr); db.flush()

    kitchen_pr = (
        db.query(Printer)
        .filter(Printer.branch_id == b.id, Printer.type == PrinterType.KITCHEN)
        .first()
    )
    if not kitchen_pr:
        kitchen_pr = Printer(
            tenant_id=t.id,
            branch_id=b.id,
            name="Kitchen Printer",
            type=PrinterType.KITCHEN,
            is_default=True,
            connection_url="http://localhost:9101/agent",
        )
        db.add(kitchen_pr); db.flush()

    # Default kitchen stations (for KOT routing)
    for st_name in ("Indian", "Chinese", "Tandoor"):
        exists = (
            db.query(KitchenStation)
            .filter(KitchenStation.branch_id == b.id, KitchenStation.name == st_name)
            .first()
        )
        if not exists:
            db.add(KitchenStation(
                tenant_id=t.id,
                branch_id=b.id,
                name=st_name,
                printer_id=kitchen_pr.id,
            ))

    # Restaurant settings (bind billing printer)
    rs = (
        db.query(RestaurantSettings)
        .filter(RestaurantSettings.tenant_id == t.id, RestaurantSettings.branch_id == b.id)
        .first()
    )
    if not rs:
        rs = RestaurantSettings(
            tenant_id=t.id,
            branch_id=b.id,
            name="WAAH Restaurant",
            logo_url=None,
            address=b.address,
            phone=b.phone,
            gstin=b.gstin,
            fssai="11223344556677",
            print_fssai_on_invoice=True,
            gst_inclusive_default=True,
            billing_printer_id=billing_pr.id,
            invoice_footer="Thank you! Visit again.",
        )
        db.add(rs)

    # Minimal RBAC bootstrap so other routers work (REPRINT/VOID/SETTINGS_EDIT/MANAGER_APPROVE)
    admin_role = (
        db.query(Role).filter(Role.tenant_id == t.id, Role.code == "ADMIN").first()
    )
    if not admin_role:
        admin_role = Role(tenant_id=t.id, code="ADMIN")
        db.add(admin_role); db.flush()

    needed_perms = ["DISCOUNT", "VOID", "REPRINT", "SETTINGS_EDIT", "MANAGER_APPROVE"]
    existing = {p.code: p for p in db.query(Permission).filter(Permission.code.in_(needed_perms)).all()}
    for code in needed_perms:
        perm = existing.get(code)
        if not perm:
            perm = Permission(code=code, description=None)
            db.add(perm); db.flush()
        rp_exists = db.query(RolePermission).filter_by(role_id=admin_role.id, permission_id=perm.id).first()
        if not rp_exists:
            db.add(RolePermission(role_id=admin_role.id, permission_id=perm.id))

    # Attach ADMIN role to the admin user
    if not db.query(UserRole).filter_by(user_id=u.id, role_id=admin_role.id).first():
        db.add(UserRole(user_id=u.id, role_id=admin_role.id))

    db.commit()
    return {
        "tenant_id": t.id,
        "branch_id": b.id,
        "admin_mobile": u.mobile,
        "admin_password": "admin",
        "admin_pin": "1234",
        "billing_printer_id": billing_pr.id,
        "kitchen_printer_id": kitchen_pr.id,
    }
