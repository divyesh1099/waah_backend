# remote_full_e2e.py
import os
import time
import uuid
import json
import random
import string
import requests
from datetime import date

BASE_URL = os.getenv("WAAH_BASE_URL", "https://waahbackend-production.up.railway.app").rstrip("/")
ADMIN_MOBILE = os.getenv("WAAH_ADMIN_MOBILE", "9999999999")
ADMIN_PASSWORD = os.getenv("WAAH_ADMIN_PASSWORD", "admin")

TIMEOUT = 30

APP_SECRET = os.getenv("WAAH_APP_SECRET") or os.getenv("APP_SECRET")

def get_token_for(mobile: str, password: str):
    r = requests.post(f"{BASE_URL}/auth/login",
                      params={"mobile": mobile, "password": password},
                      timeout=TIMEOUT)
    data = jprint(f"POST /auth/login ({mobile})", r)
    if isinstance(data, dict):
        token = data.get("access_token") or data.get("token") or data.get("jwt") or data.get("id_token")
        if token:
            return token
    print("❌ Could not find token for user:", mobile)
    print(json.dumps(data, indent=2))
    raise SystemExit(1)

def rng():
    # short readable suffix to avoid unique collisions on repeated runs
    return f"{int(time.time())}-{''.join(random.choices(string.ascii_lowercase, k=3))}"

def ok(status):
    return 200 <= status < 300

def jprint(step, r, allow=None):
    allow = allow or []
    ct = r.headers.get("content-type", "")
    body = r.json() if ct.startswith("application/json") else r.text
    flag = "✅" if ok(r.status_code) else ("⚠️" if r.status_code in allow else "❌")
    print(f"{flag} {step} [{r.status_code}]")
    if not ok(r.status_code) and r.status_code not in allow:
        # pretty print JSON errors if any
        try:
            print(json.dumps(body, indent=2))
        except Exception:
            print(body)
        raise SystemExit(1)
    return body

def jget(p, headers=None, params=None, allow=None):
    return jprint(f"GET {p}", requests.get(BASE_URL+p, headers=headers, params=params, timeout=TIMEOUT), allow)

def jpost(p, headers=None, json=None, params=None, allow=None):
    return jprint(f"POST {p}", requests.post(BASE_URL+p, headers=headers, json=json, params=params, timeout=TIMEOUT), allow)

def jdel(p, headers=None, params=None, allow=None):
    return jprint(f"DELETE {p}", requests.delete(BASE_URL+p, headers=headers, params=params, timeout=TIMEOUT), allow)

def get_token():
    # API uses POST /auth/login with query params (per your logs)
    r = requests.post(f"{BASE_URL}/auth/login",
                      params={"mobile": ADMIN_MOBILE, "password": ADMIN_PASSWORD},
                      timeout=TIMEOUT)
    data = jprint("POST /auth/login", r)
    # Be tolerant to different token field names
    token = None
    if isinstance(data, dict):
        token = data.get("access_token") or data.get("token") or data.get("jwt") or data.get("id_token")
    if not token:
        print("❌ Could not find token field in login response.")
        print(json.dumps(data, indent=2))
        raise SystemExit(1)
    return token

def main():
    suffix = rng()
    # 0) health
    jget("/healthz")

        # === Onboarding Wizard (new tenant) ======================================
    if APP_SECRET:
        on_hdr = {"X-App-Secret": APP_SECRET, "Content-Type": "application/json"}
        ob_suffix = rng()
        new_mobile = f"9{random.randint(100000000, 999999999)}"   # unique mobile for new admin
        new_pass = "admin"

        # 0) status (system)
        jget("/onboard/status")

        # 1) create tenant + first admin
        ob_admin = jpost("/onboard/admin", headers=on_hdr, json={
            "tenant_name": f"Restaurant-{ob_suffix}",
            "admin_name": f"Owner {ob_suffix}",
            "mobile": new_mobile,
            "email": f"owner{ob_suffix}@example.com",
            "password": new_pass,
            "pin": "1234"
        })
        tenant_id = ob_admin["tenant_id"]

        # 2) first branch
        ob_branch = jpost("/onboard/branch", headers=on_hdr, json={
            "tenant_id": tenant_id,
            "name": f"{ob_suffix} - Main",
            "phone": "1800123000",
            "gstin": "27AAAAA0000A1Z5",
            "state_code": "MH",
            "address": "Road 1"
        })
        branch_id = ob_branch["branch_id"]

        # 3) branch restaurant settings
        jpost("/onboard/restaurant", headers=on_hdr, json={
            "tenant_id": tenant_id, "branch_id": branch_id,
            "name": f"Restaurant {ob_suffix} - Main",
            "address": "Road 1", "phone": "1800123000",
            "gstin": "27AAAAA0000A1Z5", "fssai": "11223344556677",
            "print_fssai_on_invoice": True, "gst_inclusive_default": True,
            "service_charge_mode": "NONE", "service_charge_value": 0,
            "packing_charge_mode": "NONE", "packing_charge_value": 0,
            "invoice_footer": "Thank you!"
        })

        # 4) printers + stations
        ob_pr = jpost("/onboard/printers", headers=on_hdr, json={
            "tenant_id": tenant_id, "branch_id": branch_id,
            "billing": {
                "name": "Billing",
                "connection_url": "http://localhost:9100/agent",
                "is_default": True,
                "cash_drawer_enabled": True,
                "cash_drawer_code": "PULSE_2_100"
            },
            "kitchen": [
                {"name": "Kitchen-1", "connection_url": "http://localhost:9101/agent",
                 "is_default": True, "stations": ["Indian","Chinese"]},
                {"name": "Tandoor-PRN", "connection_url": "http://localhost:9102/agent",
                 "stations": ["Tandoor"]}
            ]
        })
        ob_stations = (ob_pr.get("created", {}) or {}).get("stations", [])
        station_id_ob = ob_stations[0] if ob_stations else None

        # 5) finish + verify status
        jpost("/onboard/finish", headers=on_hdr, json={"tenant_id": tenant_id})
        st = jget("/onboard/status", params={"tenant_id": tenant_id})
        # soft-assert completion
        if isinstance(st, dict) and not st.get("completed"):
            print("⚠️  Onboard status not completed:", json.dumps(st, indent=2))

        # 6) login as the NEW tenant admin and perform a quick smoke
        tkn2 = get_token_for(new_mobile, new_pass)
        hdr2 = {"Authorization": f"Bearer {tkn2}", "Content-Type": "application/json"}

        # Open shift for this branch
        sh2 = jpost("/shift/open", headers=hdr2, params={"branch_id": branch_id, "opening_float": 500.0})
        shift2_id = sh2["shift_id"]

        # Minimal menu & order under this tenant/branch
        cat2 = jpost("/menu/categories", headers=hdr2, json={
            "tenant_id": tenant_id, "branch_id": branch_id, "name": f"Quick-{ob_suffix}", "position": 1
        })
        item2 = jpost("/menu/items", headers=hdr2, json={
            "tenant_id": tenant_id, "category_id": cat2["id"], "name": f"Tea-{ob_suffix}",
            "tax_inclusive": True, "kitchen_station_id": station_id_ob
        })
        var2 = jpost("/menu/variants", headers=hdr2, json={
            "item_id": item2["id"], "label": "Cup", "base_price": 20.0, "is_default": True
        })

        order2 = jpost("/orders/", headers=hdr2, json={
            "tenant_id": tenant_id, "branch_id": branch_id,
            "order_no": int(time.time()), "channel": "DINE_IN", "pax": 1
        })
        jpost(f"/orders/{order2['id']}/items", headers=hdr2, json={
            "order_id": order2["id"], "item_id": item2["id"], "variant_id": var2["id"],
            "qty": 1, "unit_price": 20.0
        })
        jpost(f"/orders/{order2['id']}/pay", headers=hdr2, json={
            "order_id": order2["id"], "mode": "CASH", "amount": 20.0, "ref_no": None
        })
        jpost(f"/orders/{order2['id']}/invoice", headers=hdr2)

        # Close shift (allow RBAC variance)
        rclose = requests.post(f"{BASE_URL}/shift/{shift2_id}/close",
                               headers=hdr2,
                               params={"expected_cash": 520.0, "actual_cash": 520.0, "note": "OK"},
                               timeout=TIMEOUT)
        jprint("POST /shift/{id}/close (onboard tenant)", rclose, allow=[403])

    else:
        print("⚠️  Skipping Onboarding E2E (set WAAH_APP_SECRET or APP_SECRET to enable).")

    # 1) login
    token = get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # 2) Settings & Printers (mostly read; bootstrap already seeded one)
    # Create extra printers/station so create endpoints are exercised too.
    bill = jpost("/settings/printers", headers=headers, json={
        "tenant_id":"", "branch_id":"", "name":f"Billing-{suffix}", "type":"BILLING",
        "connection_url":"http://localhost:9100/agent", "is_default": False,
        "cash_drawer_enabled": True, "cash_drawer_code": "PULSE_2_100"
    })
    bill_pr_id = bill.get("id", None)

    kit = jpost("/settings/printers", headers=headers, json={
        "tenant_id":"", "branch_id":"", "name":f"Kitchen-{suffix}", "type":"KITCHEN",
        "connection_url":"http://localhost:9101/agent", "is_default": False
    })
    kit_pr_id = kit.get("id", None)

    station = jpost("/settings/stations", headers=headers, json={
        "tenant_id":"", "branch_id":"", "name":f"Tandoor-{suffix}", "printer_id": kit_pr_id
    })
    station_id = station.get("id")

    # Ensure restaurant exists (read, not fail if not customized)
    jget("/settings/restaurant", headers=headers, params={"tenant_id":"", "branch_id":""})

    # 3) Menu + Modifiers
    cat_main = jpost("/menu/categories", headers=headers, json={
        "tenant_id":"", "branch_id":"", "name":f"Starters-{suffix}", "position":1
    })
    cat_id = cat_main["id"]

    item = jpost("/menu/items", headers=headers, json={
        "tenant_id":"", "category_id": cat_id, "name":f"Paneer Tikka-{suffix}",
        "sku":f"PT-{suffix}", "hsn":"2106", "tax_inclusive": True, "kitchen_station_id": station_id
    })
    item_id = item["id"]

    variant = jpost("/menu/variants", headers=headers, json={
        "item_id": item_id, "label":"Full", "base_price": 220.0, "is_default": True
    })
    variant_id = variant["id"]

    jpost(f"/menu/items/{item_id}/update_tax", headers=headers,
          params={"gst_rate":5.0, "tax_inclusive": True})

    # Modifiers
    mg = jpost("/menu/modifier_groups", headers=headers, json={
        "tenant_id":"", "name": f"Toppings-{suffix}", "min_sel":0, "max_sel":3
    })
    mg_id = mg["id"]
    m1 = jpost("/menu/modifiers", headers=headers, json={"group_id": mg_id, "name":"Extra Cheese", "price_delta": 40.0})
    m2 = jpost("/menu/modifiers", headers=headers, json={"group_id": mg_id, "name":"Olives", "price_delta": 30.0})
    jpost(f"/menu/items/{item_id}/modifier_groups", headers=headers, json={"group_id": mg_id})

    # 4) Dining & Customer
    table = jpost("/dining/tables", headers=headers, json={"branch_id":"", "code": f"T5-{suffix}", "zone":"AC Hall", "seats":4})
    table_id = table["id"]

    cust = jpost("/customers/", headers=headers, json={
        "tenant_id":"", "name": f"Test Customer {suffix}", "phone": f"98{int(time.time())%10_000_000:07d}"
    })
    cust_id = cust["id"]

    # 5) Shift open
    sh = jpost("/shift/open", headers=headers, params={"branch_id":"", "opening_float": 1500.0})
    shift_id = sh["shift_id"]

    # 6) Order lifecycle with discount, cancel, split pay, invoice
    order = jpost("/orders/", headers=headers, json={
        "tenant_id":"", "branch_id":"", "order_no": int(time.time()),
        "channel":"DINE_IN", "pax":2, "table_id": table_id, "customer_id": cust_id, "note":"Window table"
    })
    order_id = order["id"]

    # Add one “to cancel” line
    coke_cat = jpost("/menu/categories", headers=headers, json={
        "tenant_id":"", "branch_id":"", "name":f"Cold-{suffix}", "position":2
    })
    coke_item = jpost("/menu/items", headers=headers, json={
        "tenant_id":"", "category_id": coke_cat["id"], "name":f"Coke-{suffix}", "kitchen_station_id": None, "gst_rate": 5.0
    })
    coke_var = jpost("/menu/variants", headers=headers, json={
        "item_id": coke_item["id"], "label":"Regular", "base_price": 50.0, "is_default": True
    })

    line_coke = jpost(f"/orders/{order_id}/items", headers=headers, json={
        "order_id": order_id, "item_id": coke_item["id"], "variant_id": coke_var["id"], "qty": 1, "unit_price": 50.0
    })
    coke_line_id = line_coke["id"]

    line_main = jpost(f"/orders/{order_id}/items", headers=headers, json={
        "order_id": order_id, "item_id": item_id, "variant_id": variant_id, "qty": 1, "unit_price": 220.0,
        "modifiers":[
            {"modifier_id": m1["id"], "qty":1, "price_delta":40.0},
            {"modifier_id": m2["id"], "qty":1, "price_delta":30.0},
        ]
    })
    pizza_line_id = line_main["id"]

    # Cancel the coke line (with reason)
    jdel(f"/orders/{order_id}/items/{coke_line_id}", headers=headers, params={"reason":"Customer changed mind"})

    # Discount the pizza line
    jpost(f"/orders/{order_id}/items/{pizza_line_id}/apply_discount", headers=headers, json={
        "discount": 20.0, "reason":"Manager Offer"
    })

    # Read order total, then split pay (UPI + CASH)
    od = jget(f"/orders/{order_id}", headers=headers)
    total_due = od.get("total_due", 350.0)
    pay_upi = 150.0
    pay_cash = float(total_due) - pay_upi
    jpost(f"/orders/{order_id}/pay", headers=headers, json={"order_id": order_id, "mode":"UPI", "amount": pay_upi, "ref_no":"gpay12345"})
    jpost(f"/orders/{order_id}/pay", headers=headers, json={"order_id": order_id, "mode":"CASH", "amount": pay_cash, "ref_no": None})

    inv = jpost(f"/orders/{order_id}/invoice", headers=headers)
    invoice_id = inv.get("invoice_id")

    # Print invoice (reprint with reason), open drawer
    if invoice_id:
        jpost(f"/print/invoice/{invoice_id}", headers=headers, params={"reason":"Customer request"})
    jpost("/print/open_drawer", headers=headers)

    # 7) KOT: explicit ticket, reprint, cancel (auto-KOT likely already happened)
    ticket = jpost("/kot/tickets", headers=headers, params={
        "order_id": order_id, "ticket_no": int(time.time()), "target_station": station_id
    })
    ticket_id = ticket.get("ticket_id")
    if ticket_id:
        jpost(f"/kot/{ticket_id}/reprint", headers=headers, params={"reason":"Blurred"})
        jpost(f"/kot/{ticket_id}/cancel", headers=headers, params={"reason":"Cancelled"})

    # 8) Online webhook (Zomato) + optional status
    online = jpost("/online/webhooks/zomato", headers=headers, json={
        "order_id": f"Z-{int(time.time())}", "tenant_id":"", "branch_id":""
    })
    if isinstance(online, dict) and "order_id" in online:
        maybe = f"/online/orders/{online['order_id']}/status"
        # Some trees may not implement; allow 404/405
        jpost(maybe, headers=headers, params={"status":"READY"}, allow=[404,405])

    # 9) Inventory: ingredients, purchase, recipe, low-stock, snapshots
    ing1 = jpost("/inventory/ingredients", headers=headers, json={
        "tenant_id":"", "name":f"Paneer-{suffix}", "uom":"g", "min_level":500
    })["id"]
    ing2 = jpost("/inventory/ingredients", headers=headers, json={
        "tenant_id":"", "name":f"Masala-{suffix}", "uom":"g", "min_level":200
    })["id"]
    jpost("/inventory/purchase", headers=headers, json={
        "tenant_id":"", "supplier":"Fresh Farms", "note":f"Stock {suffix}",
        "lines":[{"ingredient_id": ing1, "qty": 2000, "unit_cost":0.5},
                 {"ingredient_id": ing2, "qty": 1000, "unit_cost":0.2}]
    })
    jpost("/inventory/recipe", headers=headers, json={
        "item_id": item_id, "lines":[{"ingredient_id": ing1, "qty":200}, {"ingredient_id": ing2, "qty":30}]
    })
    jget("/inventory/low_stock", headers=headers)

    # 10) Reports
    today = date.today().isoformat()
    jpost("/reports/daily_sales/refresh", headers=headers, params={"day": today, "branch_id": ""})
    jpost("/reports/stock_snapshot/refresh", headers=headers, params={"day": today})

    # 11) Backup
    cfg = jpost("/backup/config", headers=headers, json={
        "tenant_id":"", "branch_id":"", "provider":"NONE",
        "local_dir":"./backups", "endpoint":None, "bucket":None,
        "access_key":None, "secret_key":None, "schedule_cron":"0 3 * * *",
    })
    cfg_id = cfg.get("id")
    if cfg_id:
        jpost("/backup/run", headers=headers, params={"config_id": cfg_id, "ok": True, "bytes_total": 12345, "location": "./backups/demo.zip"})
        jget("/backup/runs", headers=headers, params={"config_id": cfg_id})

    # 12) Users / Roles
    manager = jpost("/users/", headers=headers, json={
        "tenant_id":"", "name":f"Manager {suffix}",
        "mobile": f"98{int(time.time())%10_000_000:07d}",
        "email": f"mgr{suffix}@example.com",
        "password":"secret", "pin":"1234", "roles":["ADMIN"]
    })
    jget("/users/", headers=headers, params={"tenant_id": ""})
    roles = jget("/users/roles", headers=headers, params={"tenant_id": ""})
    admin_role_id = None
    if isinstance(roles, list):
        for r in roles:
            if r.get("code") == "ADMIN":
                admin_role_id = r.get("id")
                break
    if admin_role_id:
        jpost(f"/users/roles/{admin_role_id}/grant", headers=headers, json={"permissions": ["SHIFT_CLOSE","SETTINGS_EDIT"]})
        # Some trees don’t expose revoke; allow 404/405
        jdel(f"/users/roles/{admin_role_id}/revoke/SHIFT_CLOSE", headers=headers, allow=[404,405])

    # 13) Sync
    jpost("/sync/push", headers=headers, json={
        "device_id": str(uuid.uuid4()),
        "ops":[{"entity":"note","entity_id":str(uuid.uuid4()),"op":"UPSERT","payload":{"hello":"world"}}]
    })
    jget("/sync/pull", headers=headers, params={"since":0,"limit":10})

    # 14) Shift close with mismatch tolerance
    # Add a payout to complicate cash math
    jpost(f"/shift/{shift_id}/payout", headers=headers, params={"amount":100.0, "reason":"Groceries"})
    # Try close; if your RBAC denies, allow 403
    # We don't know exact expected cash now; read note above. Using a safe pair:
    r = requests.post(f"{BASE_URL}/shift/{shift_id}/close",
                      headers=headers,
                      params={"expected_cash": 1550.0, "actual_cash": 1500.0, "note": "Short by 50"},
                      timeout=TIMEOUT)
    jprint("POST /shift/{id}/close", r, allow=[403])

    print("\n🎉 Remote E2E completed.")

if __name__ == "__main__":
    main()
