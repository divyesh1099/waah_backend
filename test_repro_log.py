
import sys
import os
sys.path.append(os.getcwd())
from app.db import SessionLocal
from app.models.core import Tenant, User

def test_manual_flow():
    db = SessionLocal()
    try:
        t = Tenant(name="DebugTenant")
        db.add(t)
        db.flush()
        
        u = User(
            tenant_id=t.id,
            name="Admin",
            username="admin_debug_2",
            mobile="8888888888",
            pass_hash="hash",
            active=True
        )
        db.add(u)
        db.flush()
        
    except Exception as e:
        with open("error.log", "w") as f:
            f.write(str(e))
    finally:
        db.close()

if __name__ == "__main__":
    test_manual_flow()
