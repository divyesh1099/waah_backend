
import sys
import traceback
# Add current dir
import os
sys.path.append(os.getcwd())

from fastapi.testclient import TestClient
from app.main import app

def test_onboard_flow():
    try:
        client = TestClient(app)
        
        # 1. Check status
        print("Checking status...")
        r = client.get("/onboard/status")
        print(f"Status: {r.status_code} {r.text}")
        
        # 2. Create Admin
        import time
        rnd = int(time.time())
        payload = {
            "tenant_name": f"TestTenant_{rnd}",
            "admin_name": "Admin",
            "mobile": f"98{rnd%100000000:08d}",
            "password": "password",
            "pin": "1234"
        }
        headers = {"X-App-Secret": "change-me-64b-random"}
        
        print("Creating admin...")
        r = client.post("/onboard/admin", json=payload, headers=headers)
        print(f"Admin Response: {r.status_code} {r.text}")
        
        if r.status_code != 200:
            return

        data = r.json()
        tenant_id = data["tenant_id"]
        
        # 3. Create Branch
        print("Creating branch...")
        payload = {
            "tenant_id": tenant_id,
            "name": "Main Branch",
            "phone": "1234567890"
        }
        r = client.post("/onboard/branch", json=payload, headers=headers)
        print(f"Branch Response: {r.status_code} {r.text}")
        
    except Exception:
        traceback.print_exc()

if __name__ == "__main__":
    test_onboard_flow()
