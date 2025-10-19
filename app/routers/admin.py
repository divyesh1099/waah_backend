from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import settings
from app.models.core import Tenant, Branch, User
from app.util.security import hash_pw
from app.deps import require_auth

router = APIRouter(prefix="/admin", tags=["admin"])

@router.post("/dev-bootstrap")
def dev_bootstrap(db: Session = Depends(get_db)):
    if settings.APP_ENV != "dev":
        raise HTTPException(403, detail="Not allowed")
    # create tenant, branch, user if none
    t = db.query(Tenant).first()
    if not t:
        t = Tenant(name="Demo Tenant"); db.add(t); db.flush()
    b = db.query(Branch).first()
    if not b:
        b = Branch(tenant_id=t.id, name="Main Branch"); db.add(b)
    u = db.query(User).first()
    if not u:
        u = User(tenant_id=t.id, name="Admin", mobile="9999999999", email="admin@example.com", pass_hash=hash_pw("admin"))
        db.add(u)
    db.commit()
    return {"tenant_id": t.id, "branch_id": b.id, "admin_mobile": u.mobile, "admin_password": "admin"}

