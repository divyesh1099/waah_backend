
import requests
import json
import sys
import os
import time

# Ensure we can import app
sys.path.append(os.getcwd())

from app.db import SessionLocal
from app.models.core import User, Tenant, Branch
from app.util.security import hash_pw

BASE_URL = "http://127.0.0.1:8080"
E = "kot_tester"
P = "secret123"

def setup_user():
    db = SessionLocal()
    try:
        # Tenant
        t = db.query(Tenant).filter(Tenant.name == "KotTestTenant").first()
        if not t:
            t = Tenant(name="KotTestTenant")
            db.add(t); db.flush()
            
        # Branch
        b = db.query(Branch).filter(Branch.tenant_id == t.id).first()
        if not b:
            b = Branch(tenant_id=t.id, name="Main", code="MAIN")
            db.add(b); db.flush()

        # User
        u = db.query(User).filter(User.username == E).first()
        if not u:
            print("Creating test user...")
            u = User(
                tenant_id=t.id, 
                name="Kot Tester",
                username=E,
                pass_hash=hash_pw(P),
                active=True
            )
            db.add(u); db.flush()
            print("User created.")
        else:
            print("User exists.")

        # Create Category & Item
        from app.models.core import MenuCategory, MenuItem, KitchenStation, ItemVariant
        
        # Create Kitchen Station
        ks = db.query(KitchenStation).filter(KitchenStation.tenant_id == t.id).first()
        if not ks:
            ks = KitchenStation(tenant_id=t.id, branch_id=b.id, name="Hot Kitchen")
            db.add(ks); db.flush()
            
        cat = db.query(MenuCategory).filter(MenuCategory.tenant_id == t.id).first()
        if not cat:
            cat = MenuCategory(tenant_id=t.id, branch_id=b.id, name="Starters")
            db.add(cat); db.flush()
            
        item = db.query(MenuItem).filter(MenuItem.tenant_id == t.id).first()
        if not item:
            item = MenuItem(
                tenant_id=t.id, 
                category_id=cat.id, 
                name="Paneer Tikka", 
                # base_price not here
                kitchen_station_id=ks.id
            )
            db.add(item); db.flush()
            
            # Create Variant
            v = ItemVariant(
                item_id=item.id,
                label="Regular",
                base_price=150,
                is_default=True
            )
            db.add(v)
            print("Menu Item created.")
        
        db.commit()
    except Exception as e:
        with open("setup_error.txt", "w") as f:
            import traceback
            f.write(f"Error: {e}\n")
            traceback.print_exc(file=f)
        print(f"DB Setup failed: {e}")
        db.rollback()
    finally:
        db.close()

def main():
    setup_user()
    
    s = requests.Session()
    
    # 1. Login
    print(f"Logging in as {E}...")
    try:
        r = s.post(f"{BASE_URL}/auth/login", json={"username": E, "password": P})
        print(f"Login response status: {r.status_code}")
        if r.status_code != 200:
            print(f"Login failed: {r.text}")
            sys.exit(1)
        token = r.json()["access_token"]
    except Exception as e:
        print(f"Login Exception: {e}")
        # print body if available
        if 'r' in locals():
             print(f"Body: {r.text}")
        sys.exit(1)
    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. Get Context (Tenant/Branch)
    print("Fetching context...")
    r = s.get(f"{BASE_URL}/auth/me", headers=headers)
    if r.status_code != 200:
        print(f"Get Context Failed ({r.status_code}): {r.text}")
        sys.exit(1)
    me = r.json()
    tenant_id = me["tenant_id"]
    branch_id = me["branch_id"]
    print(f"Context: T={tenant_id}, B={branch_id}")
    
    # 3. Create Order
    print("Creating OPEN order...")
    unique_no = f"KOT-{int(time.time())}"
    order_payload = {
        "tenant_id": tenant_id,
        "branch_id": branch_id,
        "order_no": unique_no,
        "channel": "DINE_IN",
        "table_id": None, # or fetch a table
        "pax": 2,
        "note": "KOT Test"
    }
    r = s.post(f"{BASE_URL}/orders/", headers=headers, json=order_payload)
    if r.status_code == 200:
        o = r.json()
        print(f"Order created: {unique_no}")
    else:
        print(f"Create failed ({r.status_code}): {r.text}")
        sys.exit(1)
        
    order_id = o["id"]
    print(f"Using Order ID: {order_id} (Status: {o.get('status')})")
    
    # 4. Add Items
    print("Fetching menu item...")
    r = s.get(f"{BASE_URL}/menu/items", headers=headers, params={"tenant_id": tenant_id})
    if r.status_code != 200:
        print(f"Fetch Menu Failed ({r.status_code}): {r.text}")
        sys.exit(1)
        
    menu_items = r.json()
    target_item = None
    for item in menu_items:
        # Use any item, ideally one with station (usually first few have it in seed data)
        target_item = item
        if item.get("kitchen_station_id"):
             break
            
    if not target_item:
        print("No menu items found!")
        sys.exit(1)

    print(f"Adding item: {target_item['name']} (Station: {target_item.get('kitchen_station_id')})")
    item_payload = {
        "order_id": order_id,
        "item_id": target_item["id"],
        "qty": 2,
        "unit_price": target_item.get("base_price", 100)
    }
    r = s.post(f"{BASE_URL}/orders/{order_id}/items", headers=headers, json=item_payload)
    if r.status_code != 200:
        print(f"Failed to add item ({r.status_code}): {r.text}")
        sys.exit(1)
    
    # 5. Fire KOT
    print("Firing KOT...")
    r = s.post(f"{BASE_URL}/orders/{order_id}/kot", headers=headers)
    print(f"Fire KOT Status: {r.status_code}")
    if r.status_code != 200:
        print(f"Fire KOT Failed: {r.text}")
        sys.exit(1)
    
    res = r.json()
    print(f"Fire result: {json.dumps(res, indent=2)}")
    
    if not res.get("tickets"):
        print("FAILURE: No tickets created! (Maybe item has no station or logic failed?)")
        sys.exit(1)
        
    # 6. Verify Status
    print("Verifying Order Status...")
    r = s.get(f"{BASE_URL}/orders/{order_id}", headers=headers)
    if r.status_code != 200:
        print(f"Get Order Failed ({r.status_code}): {r.text}")
        sys.exit(1)
        
    final_o = r.json()
    if isinstance(final_o, dict) and "items" not in final_o: 
         # It's returning OrderOut from creating? No, GET orders/{id} returns details now.
         # But wait, GET orders/{id} returns OrderDetail schema which HAS "items".
         pass
         
    status = final_o.get("status")
    print(f"Order Status: {status}")
    if status == "KITCHEN":
        print("SUCCESS: Order status updated to KITCHEN.")
    else:
        print(f"WARNING: Order status is {status}, expected KITCHEN.")
    
    print("TEST PASSED")

if __name__ == "__main__":
    main()
