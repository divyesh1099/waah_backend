
import sys
import os
import traceback
sys.path.append(os.getcwd())
from fastapi.testclient import TestClient
from app.main import app
from app.util.security import hash_pw
from app.db import SessionLocal
from app.models.core import User, Tenant, Branch
import time

def verify_login_flow():
    client = TestClient(app)
    
    db = SessionLocal()
    rnd = int(time.time())
    uname = f"login_test_{rnd}"
    try:
        t = Tenant(name=f"LoginTestTenant_{rnd}")
        db.add(t); db.flush()
        
        b = Branch(
             tenant_id=t.id, 
             name="Main Branch", 
             code="B1" 
             # other fields nullable or have defaults
        )
        db.add(b); db.flush()
        
        hashed = hash_pw("secret123")
        u = User(
            tenant_id=t.id, 
            name="LoginUser",
            username=uname,
            pass_hash=hashed,
            active=True
        )
        db.add(u); db.flush()
        db.commit()
    except Exception as e:
        print(f"Setup failed: {e}")
        traceback.print_exc()
        db.rollback()
        return
    finally:
        db.close()
        
    print(f"User: {uname}")
    
    try:
        params = {"username": uname, "password": "secret123"}
        print("Attempting with query params...")
        r = client.post("/auth/login", params=params)
        print(f"Response: {r.status_code}")
        if r.status_code != 200:
             print(r.text)
        
    except Exception:
        traceback.print_exc()

if __name__ == "__main__":
    verify_login_flow()
