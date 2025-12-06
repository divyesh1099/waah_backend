
import sys
import os
sys.path.append(os.getcwd())
from fastapi.testclient import TestClient
from app.main import app
from app.util.security import hash_pw
from app.db import SessionLocal
from app.models.core import User, Tenant

def verify_login_flow():
    client = TestClient(app)
    
    # 1. Manually create user with KNOWN password
    db = SessionLocal()
    try:
        t = Tenant(name="LoginTestTenant")
        db.add(t); db.flush()
        
        hashed = hash_pw("secret123")
        u = User(
            tenant_id=t.id, 
            name="LoginUser",
            username="login_test_user",
            pass_hash=hashed,
            active=True
        )
        db.add(u); db.flush()
        db.commit()
        print(f"Created user {u.username} with password 'secret123'")
    except Exception as e:
        print(f"Setup failed: {e}")
        db.rollback()
        return
    finally:
        db.close()
        
    # 2. Try login via API
    try:
        payload = {"username": "login_test_user", "password": "secret123"}
        r = client.post("/auth/login", json=payload)
        print(f"Login Response: {r.status_code}")
        if r.status_code == 200:
            print("Login SUCCESS")
            print(r.json())
        else:
            print("Login FAILED")
            print(r.text)
    except Exception as e:
        print(f"Login exception: {e}")

if __name__ == "__main__":
    verify_login_flow()
