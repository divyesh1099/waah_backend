
import sys
import os
sys.path.append(os.getcwd())
from sqlalchemy.orm import Session
from app.db import engine, SessionLocal
from app.models.core import Tenant, User, Role, Permission, RolePermission, UserRole, OnboardProgress

def test_manual_flow():
    db = SessionLocal()
    try:
        print("Creating Tenant...")
        t = Tenant(name="DebugTenant")
        db.add(t)
        db.flush()
        print(f"Tenant created: {t.id}")
        
        print("Creating User...")
        u = User(
            tenant_id=t.id,
            name="Admin",
            username="admin_debug",
            mobile="9999999999",
            pass_hash="hash",
            active=True
        )
        # Note: I am NOT setting pin_hash, ensuring it defaults correctly or if specific args are needed.
        # onboard.py sets branch_id=None implicitly? logic says branch_id is Mapped[str | None].
        
        db.add(u)
        db.flush()
        print(f"User created: {u.id}")
        
        print("Rolling back...")
        db.rollback()
        print("Success")
    except Exception as e:
        print("FAILED!")
        print(e)
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    test_manual_flow()
