# full_flow_test_v2.py
import os, sys, time, uuid, random, string
from datetime import datetime, date, timedelta, timezone
import httpx

BASE = os.environ.get("WAAH_BASE", "http://localhost:8000")
HDRS = {}
RANDOM = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))

def p(step, r):
    ok = 200 <= r.status_code < 300
    print(f"→ {step}: {r.status_code}", end=" ")
    try:
        print(r.json())
    except Exception:
        print(r.text)
    if not ok:
        raise SystemExit(f"FAILED at {step} ({r.status_code}): {r.text}")
    return r.json() if ok else None

def get(path, **kw):
    return httpx.get(f"{BASE}{path}", headers=HDRS, **kw)

def post(path, **kw):
    # always JSON unless caller overrides
    if "json" not in kw and "data" not in kw:
        kw["json"] = {}
    return httpx.post(f"{BASE}{path}", headers=HDRS, **kw)

def main():
    print(f"\n=== WAAH Full Flow Smoke Test @ {BASE} ===\n")

    # 0) Health
    p("healthz", get("/healthz"))

    # 1) Dev bootstrap
    boot = p("admin/dev-bootstrap", post("/admin/dev-bootstrap"))

    tenant_id = boot["tenant_id"]
    branch_id  = boot["branch_id"]

    # 2) Login (password)
    tok = p("auth/login", post("/auth/login", params={"mobile": "9999999999", "password": "admin"}))
    HDRS.update({"Authorization": f"Bearer {tok['access_token']}"})

    # 3) Settings — printers (incl. cash drawer fields) + station + restaurant
    bill_pr = p("settings/printers (billing)", post("/settings/printers", json={
        "tenant_id": tenant_id,
        "branch_id": branch_id,
        "name": f"Billing-{RANDOM}",
        "type": "BILLING",
        "connection_url": "http://localhost:9100/agent",
        "is_default": True,
        "cash_drawer_enabled": True,
        "cash_drawer_code": "PULSE_2_100",
    }))["id"]

    kit_pr = p("settings/printers (kitchen)", post("/settings/printers", json={
        "tenant_id": tenant_id,
        "branch_id": branch_id,
        "name": f"Kitchen-{RANDOM}",
        "type": "KITCHEN",
        "connection_url": "http://localhost:9101/agent",
        "is_default": True
    }))["id"]

    station = p("settings/stations", post("/settings/stations", json={
        "tenant_id": tenant_id,
        "branch_id": branch_id,
        "name": f"Tandoor-{RANDOM}",
        "printer_id": kit_pr
    }))["id"]

    rs = p("settings/restaurant (upsert)", post("/settings/restaurant", json={
        "tenant_id": tenant_id,
        "branch_id": branch_id,
        "name": f"Waah Test {RANDOM}",
        "logo_url": None,
        "address": "123 Food Street",
        "phone": "1800123456",
        "gstin": "27ABCDE1234F2Z5",
        "fssai": "11223344556677",
        "print_fssai_on_invoice": True,
        "gst_inclusive_default": True,
        "service_charge_mode": "NONE",
        "service_charge_value": 0,
        "packing_charge_mode": "NONE",
        "packing_charge_value": 0,
        "billing_printer_id": bill_pr,
        "invoice_footer": "Thank you! Visit again."
    }))["id"]

    p("settings/restaurant (get)", get("/settings/restaurant", params={
        "tenant_id": tenant_id, "branch_id": branch_id
    }))

    # 4) Menu — category, item, variant, tax, station
    cat = p("menu/categories (create)", post("/menu/categories", json={
        "tenant_id": tenant_id, "branch_id": branch_id,
        "name": "Starters", "position": 1
    }))["id"]

    p("menu/categories (list)", get("/menu/categories", params={
        "tenant_id": tenant_id, "branch_id": branch_id
    }))

    item = p("menu/items (create)", post("/menu/items", json={
        "tenant_id": tenant_id,
        "category_id": cat,
        "name": "Paneer Tikka",
        "sku": "PT001",
        "hsn": "2106",
        "tax_inclusive": True
    }))["id"]

    p("menu/items/assign_station", post(f"/menu/items/{item}/assign_station",
       params={"station_id": station}))

    variant = p("menu/variants (create)", post("/menu/variants", json={
        "item_id": item, "label": "Full", "base_price": 220.0, "is_default": True
    }))["id"]

    p("menu/items/update_tax", post(f"/menu/items/{item}/update_tax",
       params={"gst_rate": 5.0, "tax_inclusive": True}))

    # 5) Inventory — ingredients, purchase, recipe, low stock
    ing1 = p("inventory/ingredients (1)", post("/inventory/ingredients", json={
        "tenant_id": tenant_id, "name": "Paneer", "uom": "g", "min_level": 500
    }))["id"]
    ing2 = p("inventory/ingredients (2)", post("/inventory/ingredients", json={
        "tenant_id": tenant_id, "name": "Masala", "uom": "g", "min_level": 200
    }))["id"]

    p("inventory/purchase", post("/inventory/purchase", json={
        "tenant_id": tenant_id,
        "supplier": "Fresh Farms",
        "note": "Initial stock",
        "lines": [
            {"ingredient_id": ing1, "qty": 2000, "unit_cost": 0.5},
            {"ingredient_id": ing2, "qty": 1000, "unit_cost": 0.2},
        ],
    }))

    p("inventory/recipe", post("/inventory/recipe", json={
        "item_id": item,
        "lines": [
            {"ingredient_id": ing1, "qty": 200},  # 200g paneer per plate
            {"ingredient_id": ing2, "qty": 30},   # 30g masala
        ],
    }))

    p("inventory/low_stock", get("/inventory/low_stock"))

    # 6) Shift — open, cash moves
    shift = p("shift/open", post("/shift/open", params={
        "branch_id": branch_id, "opening_float": 1000.0
    }))["shift_id"]
    p("shift/payin", post(f"/shift/{shift}/payin", params={"amount": 500.0, "reason": "Float top-up"}))
    p("shift/payout", post(f"/shift/{shift}/payout", params={"amount": 100.0, "reason": "Groceries"}))

    # 7) Orders — open, add item, pay, invoice
    order = p("orders/open", post("/orders/", json={
        "tenant_id": tenant_id,
        "branch_id": branch_id,
        "order_no": int(time.time()),
        "channel": "DINE_IN",
        "pax": 2,
        "note": "Window table"
    }))["id"]

    # IMPORTANT: body MUST match OrderItemIn exactly; path MUST include order_id
    p("orders/add_item", post(f"/orders/{order}/items", json={
        "order_id": order,
        "item_id": item,
        "variant_id": variant,
        "qty": 1,
        "unit_price": 220.0
    }))

    # one more line (to exercise totals/inventory)
    p("orders/add_item (again)", post(f"/orders/{order}/items", json={
        "order_id": order,
        "item_id": item,
        "variant_id": variant,
        "qty": 2,
        "unit_price": 220.0
    }))

    pay = p("orders/pay", post(f"/orders/{order}/pay", json={
        "order_id": order,
        "mode": "CASH",
        "amount": 660.0,  # 220 * 3 (tax-inclusive), service/packing NONE
        "ref_no": None
    }))

    inv = p("orders/invoice", post(f"/orders/{order}/invoice"))
    invoice_id = inv["invoice_id"]

    # 8) Print — reprint invoice + open drawer
    p("print/invoice (reprint)", post(f"/print/invoice/{invoice_id}", params={"reason": "Customer request"}))
    p("print/open_drawer", post("/print/open_drawer"))

    # 9) KOT — create + reprint + cancel (also autokots were created via add_item, but we create one explicitly)
    ticket = p("kot/tickets", post("/kot/tickets", params={
        "order_id": order, "ticket_no": int(time.time()), "target_station": station
    }))["ticket_id"]
    p("kot/reprint", post(f"/kot/{ticket}/reprint", params={"reason": "KOT blurred"}))
    p("kot/cancel", post(f"/kot/{ticket}/cancel", params={"reason": "Order voided"}))

    # 10) Online webhook + status
    w = p("online/webhook", post("/online/webhooks/zomato", json={
        "order_id": f"Z-{int(time.time())}",
        "tenant_id": tenant_id, "branch_id": branch_id
    }))
    p("online/status", post(f"/online/orders/{w['order_id']}/status", params={"status": "READY"}))

    # 11) Reports — refresh daily sales & stock snapshot (today)
    today = date.today()
    p("reports/daily_sales/refresh", post("/reports/daily_sales/refresh",
       params={"day": today.isoformat(), "branch_id": branch_id}))
    p("reports/stock_snapshot/refresh", post("/reports/stock_snapshot/refresh",
       params={"day": today.isoformat()}))
    # (Reading snapshots is up to your app; refresh endpoints ensure tables are populated.)

    # 12) Backup — config, run, list
    cfg = p("backup/config", post("/backup/config", json={
        "tenant_id": tenant_id,
        "branch_id": branch_id,
        "provider": "NONE",
        "local_dir": "./backups",
        "endpoint": None, "bucket": None,
        "access_key": None, "secret_key": None,
        "schedule_cron": "0 3 * * *"
    }))["id"]
    p("backup/run", post("/backup/run", params={
        "config_id": cfg, "ok": True, "bytes_total": 12345, "location": "./backups/demo.zip"
    }))
    p("backup/runs", get("/backup/runs", params={"config_id": cfg}))

    # 13) Users / Roles / Permissions
    # create manager user with ADMIN role
    u = p("users/create", post("/users/", json={
        "tenant_id": tenant_id,
        "name": f"Manager {RANDOM}",
        "mobile": f"98{random.randint(10000000,99999999)}",
        "email": f"mgr{RANDOM}@example.com",
        "password": "secret",
        "pin": "1234",
        "roles": ["ADMIN"]  # will be created/attached if missing
    }))["id"]
    p("users/list", get("/users/", params={"tenant_id": tenant_id}))
    # grant extra permission to ADMIN role (id lookup: list roles)
    roles = p("roles/list", get("/users/roles", params={"tenant_id": tenant_id}))
    admin_role_id = next(r["id"] for r in roles if r["code"] == "ADMIN")
    p("roles/grant", post(f"/users/roles/{admin_role_id}/grant", json={
        "permissions": ["SHIFT_CLOSE", "SETTINGS_EDIT"]
    }))
    # revoke one as a check
    p("roles/revoke", httpx.delete(f"{BASE}/users/roles/{admin_role_id}/revoke/SHIFT_CLOSE", headers=HDRS))

    # 14) Sync — push/pull ledger
    p("sync/push", post("/sync/push", json={
        "device_id": str(uuid.uuid4()),
        "ops": [
            {"entity": "note", "entity_id": str(uuid.uuid4()), "op": "UPSERT", "payload": {"hello": "world"}}
        ]
    }))
    p("sync/pull", get("/sync/pull", params={"since": 0, "limit": 10}))

    # 15) Shift close (uses SHIFT_CLOSE perm; admin has it)
    p("shift/close", post(f"/shift/{shift}/close", params={
        "expected_cash": 1460.0, "actual_cash": 1460.0, "note": "Closing OK"
    }))

    print("\n✅ Full flow completed successfully.\n")

if __name__ == "__main__":
    main()
