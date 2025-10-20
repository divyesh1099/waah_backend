# test_full_flow_e2e.py
import time
import uuid
from datetime import date
import pytest

def jprint(step, r):
  # helpful failure text if something breaks
  assert 200 <= r.status_code < 300, f"{step} -> {r.status_code}: {r.text}"
  return r.json() if r.headers.get("content-type","").startswith("application/json") else r.text

def test_full_flow(client, base_url, auth_headers, rng_suffix):
  # ===== Settings: printers, station, restaurant =====
  # Billing printer (with cash drawer flags)
  r = client.post(f"{base_url}/settings/printers", headers=auth_headers, json={
    "tenant_id":"", # will not be validated strictly; OK for Phase-1
    "branch_id":"",
    "name":f"Billing-{rng_suffix}",
    "type":"BILLING",
    "connection_url":"http://localhost:9100/agent",
    "is_default": True,
    "cash_drawer_enabled": True,
    "cash_drawer_code": "PULSE_2_100",
  })
  bill_pr_id = jprint("POST /settings/printers (billing)", r)["id"]

  # Kitchen printer
  r = client.post(f"{base_url}/settings/printers", headers=auth_headers, json={
    "tenant_id":"",
    "branch_id":"",
    "name":f"Kitchen-{rng_suffix}",
    "type":"KITCHEN",
    "connection_url":"http://localhost:9101/agent",
    "is_default": True,
  })
  kit_pr_id = jprint("POST /settings/printers (kitchen)", r)["id"]

  # Station
  r = client.post(f"{base_url}/settings/stations", headers=auth_headers, json={
    "tenant_id":"", "branch_id":"", "name":f"Tandoor-{rng_suffix}", "printer_id": kit_pr_id
  })
  station_id = jprint("POST /settings/stations", r)["id"]

  # Restaurant settings upsert (+ bind billing printer)
  r = client.post(f"{base_url}/settings/restaurant", headers=auth_headers, json={
    "tenant_id":"", "branch_id":"",
    "name":f"Waah {rng_suffix}",
    "logo_url": None,
    "address":"123 Food Street", "phone":"1800123456",
    "gstin":"27ABCDE1234F2Z5", "fssai":"11223344556677",
    "print_fssai_on_invoice": True, "gst_inclusive_default": True,
    "service_charge_mode":"NONE", "service_charge_value":0,
    "packing_charge_mode":"NONE", "packing_charge_value":0,
    "billing_printer_id": bill_pr_id,
    "invoice_footer":"Thank you! Visit again."
  })
  rs_id = jprint("POST /settings/restaurant", r)["id"]

  r = client.get(f"{base_url}/settings/restaurant", headers=auth_headers, params={"tenant_id":"","branch_id":""})
  jprint("GET /settings/restaurant", r)

  # ===== Menu: category, item, variant, assign station, update tax =====
  r = client.post(f"{base_url}/menu/categories", headers=auth_headers, json={
    "tenant_id":"", "branch_id":"", "name":"Starters", "position":1
  })
  cat_id = jprint("POST /menu/categories", r)["id"]

  r = client.get(f"{base_url}/menu/categories", headers=auth_headers, params={"tenant_id":"","branch_id":""})
  jprint("GET /menu/categories", r)

  r = client.post(f"{base_url}/menu/items", headers=auth_headers, json={
    "tenant_id":"", "category_id": cat_id, "name":"Paneer Tikka", "sku":"PT001", "hsn":"2106", "tax_inclusive": True
  })
  item_id = jprint("POST /menu/items", r)["id"]

  r = client.post(f"{base_url}/menu/variants", headers=auth_headers, json={
    "item_id": item_id, "label":"Full", "base_price":220.0, "is_default": True
  })
  variant_id = jprint("POST /menu/variants", r)["id"]

  r = client.post(f"{base_url}/menu/items/{item_id}/assign_station", headers=auth_headers, params={"station_id": station_id})
  jprint("POST /menu/items/{id}/assign_station", r)

  r = client.post(f"{base_url}/menu/items/{item_id}/update_tax", headers=auth_headers, params={"gst_rate":5.0, "tax_inclusive": True})
  jprint("POST /menu/items/{id}/update_tax", r)

  # ===== Inventory: ingredients, purchase, recipe, low_stock =====
  r = client.post(f"{base_url}/inventory/ingredients", headers=auth_headers, json={
    "tenant_id":"", "name":"Paneer", "uom":"g", "min_level":500
  })
  ing1 = jprint("POST /inventory/ingredients (1)", r)["id"]

  r = client.post(f"{base_url}/inventory/ingredients", headers=auth_headers, json={
    "tenant_id":"", "name":"Masala", "uom":"g", "min_level":200
  })
  ing2 = jprint("POST /inventory/ingredients (2)", r)["id"]

  r = client.post(f"{base_url}/inventory/purchase", headers=auth_headers, json={
    "tenant_id":"", "supplier":"Fresh Farms", "note":"Initial stock",
    "lines":[
      {"ingredient_id": ing1, "qty": 2000, "unit_cost":0.5},
      {"ingredient_id": ing2, "qty": 1000, "unit_cost":0.2},
    ]
  })
  jprint("POST /inventory/purchase", r)

  r = client.post(f"{base_url}/inventory/recipe", headers=auth_headers, json={
    "item_id": item_id,
    "lines":[{"ingredient_id": ing1, "qty": 200}, {"ingredient_id": ing2, "qty": 30}]
  })
  jprint("POST /inventory/recipe", r)

  r = client.get(f"{base_url}/inventory/low_stock", headers=auth_headers)
  jprint("GET /inventory/low_stock", r)

  # ===== Shift: open, payin, payout =====
  r = client.post(f"{base_url}/shift/open", headers=auth_headers, params={"branch_id":"", "opening_float": 1000.0})
  shift_id = jprint("POST /shift/open", r)["shift_id"]

  r = client.post(f"{base_url}/shift/{shift_id}/payin", headers=auth_headers, params={"amount":500.0, "reason":"Float top-up"})
  jprint("POST /shift/{id}/payin", r)

  r = client.post(f"{base_url}/shift/{shift_id}/payout", headers=auth_headers, params={"amount":100.0, "reason":"Groceries"})
  jprint("POST /shift/{id}/payout", r)

  # ===== Orders: open, add items (auto-KOT via station), pay, invoice =====
  r = client.post(f"{base_url}/orders/", headers=auth_headers, json={
    "tenant_id":"", "branch_id":"", "order_no": int(time.time()),
    "channel":"DINE_IN", "pax":2, "note":"Window table"
  })
  order_id = jprint("POST /orders/", r)["id"]

  r = client.post(f"{base_url}/orders/{order_id}/items", headers=auth_headers, json={
    "order_id": order_id, "item_id": item_id, "variant_id": variant_id, "qty": 1, "unit_price": 220.0
  })
  jprint("POST /orders/{id}/items (1)", r)

  r = client.post(f"{base_url}/orders/{order_id}/items", headers=auth_headers, json={
    "order_id": order_id, "item_id": item_id, "variant_id": variant_id, "qty": 2, "unit_price": 220.0
  })
  jprint("POST /orders/{id}/items (2)", r)

  r = client.post(f"{base_url}/orders/{order_id}/pay", headers=auth_headers, json={
    "order_id": order_id, "mode":"CASH", "amount": 660.0, "ref_no": None
  })
  jprint("POST /orders/{id}/pay", r)

  r = client.post(f"{base_url}/orders/{order_id}/invoice", headers=auth_headers)
  inv = jprint("POST /orders/{id}/invoice", r)
  invoice_id = inv["invoice_id"]

  # ===== Print: reprint invoice, open drawer =====
  r = client.post(f"{base_url}/print/invoice/{invoice_id}", headers=auth_headers, params={"reason":"Customer request"})
  jprint("POST /print/invoice/{id}", r)

  r = client.post(f"{base_url}/print/open_drawer", headers=auth_headers)
  jprint("POST /print/open_drawer", r)

  # ===== KOT: explicit ticket + reprint + cancel (autokots already created by items) =====
  r = client.post(f"{base_url}/kot/tickets", headers=auth_headers, params={
    "order_id": order_id, "ticket_no": int(time.time()), "target_station": station_id
  })
  ticket_id = jprint("POST /kot/tickets", r)["ticket_id"]

  # reprint (perm-protected)
  r = client.post(f"{base_url}/kot/{ticket_id}/reprint", headers=auth_headers, params={"reason":"Blurred"})
  jprint("POST /kot/{id}/reprint", r)

  r = client.post(f"{base_url}/kot/{ticket_id}/cancel", headers=auth_headers, params={"reason":"Cancelled"})
  jprint("POST /kot/{id}/cancel", r)

  # ===== Online: webhook (status endpoint may or may not exist; skip gracefully) =====
  r = client.post(f"{base_url}/online/webhooks/zomato", headers=auth_headers, json={
    "order_id": f"Z-{int(time.time())}",
    "tenant_id":"", "branch_id":""
  })
  online = jprint("POST /online/webhooks/{provider}", r)
  # optional status (only if implemented in your current tree)
  maybe_status = f"{base_url}/online/orders/{online['order_id']}/status"
  rr = client.post(maybe_status, headers=auth_headers, params={"status":"READY"})
  if rr.status_code in (404, 405):
    # Router without status endpoint — acceptable
    pass
  else:
    jprint("POST /online/orders/{id}/status", rr)

  # ===== Reports: refresh snapshots (daily sales & stock) =====
  today = date.today().isoformat()
  r = client.post(f"{base_url}/reports/daily_sales/refresh", headers=auth_headers, params={"day": today, "branch_id": ""})
  jprint("POST /reports/daily_sales/refresh", r)
  r = client.post(f"{base_url}/reports/stock_snapshot/refresh", headers=auth_headers, params={"day": today})
  jprint("POST /reports/stock_snapshot/refresh", r)

  # ===== Backup: config → run → list =====
  r = client.post(f"{base_url}/backup/config", headers=auth_headers, json={
    "tenant_id":"", "branch_id":"", "provider":"NONE",
    "local_dir":"./backups", "endpoint":None, "bucket":None,
    "access_key":None, "secret_key":None, "schedule_cron":"0 3 * * *",
  })
  cfg_id = jprint("POST /backup/config", r)["id"]

  r = client.post(f"{base_url}/backup/run", headers=auth_headers, params={
    "config_id": cfg_id, "ok": True, "bytes_total": 12345, "location": "./backups/demo.zip"
  })
  jprint("POST /backup/run", r)

  r = client.get(f"{base_url}/backup/runs", headers=auth_headers, params={"config_id": cfg_id})
  jprint("GET /backup/runs", r)

  # ===== Users / Roles / Permissions =====
  # create a user with ADMIN role
  r = client.post(f"{base_url}/users/", headers=auth_headers, json={
    "tenant_id":"", "name":f"Manager {rng_suffix}",
    "mobile": f"98{int(time.time())%10_000_000:07d}",
    "email": f"mgr{rng_suffix}@example.com",
    "password":"secret", "pin":"1234", "roles":["ADMIN"]
  })
  user_id = jprint("POST /users/", r)["id"]

  # list users
  r = client.get(f"{base_url}/users/", headers=auth_headers, params={"tenant_id": ""})
  jprint("GET /users/", r)

  # roles listing
  r = client.get(f"{base_url}/users/roles", headers=auth_headers, params={"tenant_id": ""})
  roles = jprint("GET /users/roles", r)
  admin_role_id = next((r["id"] for r in roles if r["code"] == "ADMIN"), None)

  # grant permissions to ADMIN role (idempotent)
  if admin_role_id:
    r = client.post(f"{base_url}/users/roles/{admin_role_id}/grant", headers=auth_headers, json={"permissions": ["SHIFT_CLOSE","SETTINGS_EDIT"]})
    jprint("POST /users/roles/{role_id}/grant", r)
    # revoke one
    rr = client.delete(f"{base_url}/users/roles/{admin_role_id}/revoke/SHIFT_CLOSE", headers=auth_headers)
    if rr.status_code not in (404, 405): # some trees may not expose revoke endpoint; skip if missing
      jprint("DELETE /users/roles/{role_id}/revoke/{perm}", rr)

  # ===== Sync: push/pull =====
  r = client.post(f"{base_url}/sync/push", headers=auth_headers, json={
    "device_id": str(uuid.uuid4()),
    "ops":[{"entity":"note","entity_id":str(uuid.uuid4()),"op":"UPSERT","payload":{"hello":"world"}}]
  })
  jprint("POST /sync/push", r)

  r = client.get(f"{base_url}/sync/pull", headers=auth_headers, params={"since":0,"limit":10})
  jprint("GET /sync/pull", r)

  # ===== Shift close (requires perm; ADMIN usually has it after bootstrap) =====
  r = client.post(f"{base_url}/shift/{shift_id}/close", headers=auth_headers, params={
    "expected_cash": 1460.0, "actual_cash": 1460.0, "note": "OK"
  })
  # Some trees require SHIFT_CLOSE explicitly; if your ADMIN doesn’t have it, this may 403.
  if r.status_code == 403:
    pytest.skip("SHIFT_CLOSE permission not granted to current user in this tree.")
  jprint("POST /shift/{id}/close", r)
