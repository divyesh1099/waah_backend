
import sys
import os
import pytest
from fastapi.testclient import TestClient
from datetime import datetime
sys.path.append(os.getcwd())

from app.main import app
from app.util.security import hash_pw
from app.db import SessionLocal
from app.models.core import User, Tenant, Branch, Order, OrderStatus

client = TestClient(app)

def setup_data():
    db = SessionLocal()
    unique = int(datetime.now().timestamp())
    
    # Tenant
    t = Tenant(name=f"StatusTest_{unique}")
    db.add(t); db.flush()
    
    # Branch
    b = Branch(tenant_id=t.id, name="Main", code=f"B_{unique}")
    db.add(b); db.flush()
    
    # User (Admin)
    raw_pw = "password123"
    u = User(
        tenant_id=t.id, 
        name="Admin", 
        username=f"admin_{unique}", 
        pass_hash=hash_pw(raw_pw),
        active=True
    )
    db.add(u); db.flush()
    db.commit()
    
    # Login to get token
    resp = client.post("/auth/login", params={"username": u.username, "password": raw_pw})
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    
    return db, t, b, u, token

def test_order_status_lifecycle():
    db, t, b, u, token = setup_data()
    headers = {"Authorization": f"Bearer {token}"}
    
    # 1. Create Order (OPEN)
    payload = {
        "tenant_id": t.id,
        "branch_id": b.id,
        "order_no": f"ORD-{int(datetime.now().timestamp())}",
        "channel": "DINE_IN",
        "pax": 2
    }
    r = client.post("/orders/", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    order_id = r.json()["id"]
    assert r.json()["status"] == "OPEN"
    
    # 2. Change to KITCHEN
    r = client.patch(f"/orders/{order_id}/status", json={"status": "KITCHEN", "reason": "Chef started"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "KITCHEN"
    
    # 3. Change to READY
    r = client.patch(f"/orders/{order_id}/status", json={"status": "READY"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "READY"
    
     # 4. Change to SERVED
    r = client.patch(f"/orders/{order_id}/status", json={"status": "SERVED"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "SERVED"

    # 5. Change to CLOSED
    r = client.patch(f"/orders/{order_id}/status", json={"status": "CLOSED"}, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "CLOSED"
    assert data["closed_at"] is not None
    
    # 6. Try to re-open (Should Fail beause logic says so? Or check code)
    # orders.py: "If o.status in (CLOSED, VOID) and new != VOID: raise 403"
    r = client.patch(f"/orders/{order_id}/status", json={"status": "OPEN"}, headers=headers)
    assert r.status_code == 403, "Should not allow re-opening closed order"

if __name__ == "__main__":
    try:
        test_order_status_lifecycle()
        print("Test PASSED")
    except Exception as e:
        with open("status_error.txt", "w") as f:
            f.write(str(e))
            import traceback
            traceback.print_exc(file=f)
        print("Test FAILED (check status_error.txt)")
