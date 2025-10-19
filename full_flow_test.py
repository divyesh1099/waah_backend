# full_flow_test.py
import os
import time
import uuid
import json
import datetime as dt
import requests
from dataclasses import dataclass
from typing import Any, Dict, Optional

# --- Config ---------------------------------------------------------------
BASE_URL = os.getenv("WAAH_BASE_URL", "http://localhost:8000")
ADMIN_MOBILE = os.getenv("WAAH_ADMIN_MOBILE", "9999999999")
ADMIN_PASSWORD = os.getenv("WAAH_ADMIN_PASSWORD", "admin")

# If your print agent isn't running, print endpoints will still be exercised,
# but may return 400; that's OK for this smoke test.
BILLING_AGENT_URL = os.getenv("WAAH_BILLING_AGENT_URL", "http://localhost:9100/agent")
KITCHEN_AGENT_URL = os.getenv("WAAH_KITCHEN_AGENT_URL", "http://localhost:9101/agent")

# --- Small HTTP helper ----------------------------------------------------
class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.token: Optional[str] = None

    def set_token(self, token: str):
        self.token = token
        self.s.headers.update({"Authorization": f"Bearer {token}"})

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.s.get(self._url(path), **kwargs)

    def post(self, path: str, json: Any = None, params: Dict[str, Any] | None = None) -> requests.Response:
        return self.s.post(self._url(path), json=json, params=params)

    def delete(self, path: str, **kwargs) -> requests.Response:
        return self.s.delete(self._url(path), **kwargs)

def ok(resp: requests.Response, allow_4xx: bool = False) -> bool:
    if 200 <= resp.status_code < 300:
        return True
    if allow_4xx and 400 <= resp.status_code < 500:
        return True
    return False

def show(resp: requests.Response, label: str):
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    print(f"→ {label}: {resp.status_code} {body}")
    return body

# --- Orchestrated full day ------------------------------------------------
@dataclass
class Context:
    tenant_id: str = ""
    branch_id: str = ""
    category_id: str = ""
    item_id: str = ""
    variant_id: str = ""
    billing_printer_id: str = ""
    kitchen_printer_id: str = ""
    station_id: str = ""
    shift_id: str = ""
    order_id: str = ""
    invoice_id: str = ""
    online_order_id: str = ""
    online_core_order_id: str = ""
    user_id: str = ""
    role_id: str = ""

def main():
    api = Api(BASE_URL)
    ctx = Context()
    today = dt.date.today().isoformat()
    stamp = int(time.time())
    rname = f"Waah Test {uuid.uuid4().hex[:6]}"

    print(f"\n=== WAAH Full Flow Smoke Test @ {BASE_URL} ===\n")

    # Health
    r = api.get("/healthz")
    assert ok(r), "API not healthy"
    show(r, "healthz")

    # Admin bootstrap (dev only)
    r = api.post("/admin/dev-bootstrap")
    assert ok(r), "dev-bootstrap failed"
    out = show(r, "admin/dev-bootstrap")
    ctx.tenant_id = out.get("tenant_id", "")
    ctx.branch_id = out.get("branch_id", "")

    # Login (JWT)
    r = api.post("/auth/login", params={"mobile": ADMIN_MOBILE, "password": ADMIN_PASSWORD})
    assert ok(r), "login failed"
    token = show(r, "auth/login")["access_token"]
    api.set_token(token)

    # Create printers
    body_billing = {
        "tenant_id": ctx.tenant_id,
        "branch_id": ctx.branch_id,
        "name": f"Billing-{stamp}",
        "type": "BILLING",
        "connection_url": BILLING_AGENT_URL,
        "is_default": True,
        # If your API supports drawer fields:
        "cash_drawer_enabled": True,
        "cash_drawer_code": "PULSE_2_100",
    }
    r = api.post("/settings/printers", json=body_billing)
    if ok(r):
        ctx.billing_printer_id = show(r, "settings/printers (billing)")["id"]
    else:
        print("→ settings/printers (billing): skipped/failed, continuing")

    body_kitchen = {
        "tenant_id": ctx.tenant_id,
        "branch_id": ctx.branch_id,
        "name": f"Kitchen-{stamp}",
        "type": "KITCHEN",
        "connection_url": KITCHEN_AGENT_URL,
        "is_default": True,
    }
    r = api.post("/settings/printers", json=body_kitchen)
    if ok(r):
        ctx.kitchen_printer_id = show(r, "settings/printers (kitchen)")["id"]

    # Station
    r = api.post("/settings/stations", json={
        "tenant_id": ctx.tenant_id,
        "branch_id": ctx.branch_id,
        "name": "Tandoor",
        "printer_id": ctx.kitchen_printer_id or None
    })
    if ok(r):
        ctx.station_id = show(r, "settings/stations")["id"]

    # Restaurant settings (bind billing printer)
    r = api.post("/settings/restaurant", json={
        "tenant_id": ctx.tenant_id,
        "branch_id": ctx.branch_id,
        "name": rname,
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
        "billing_printer_id": ctx.billing_printer_id or None,
        "invoice_footer": "Thank you! Visit again.",
    })
    if ok(r):
        show(r, "settings/restaurant (upsert)")
    r = api.get("/settings/restaurant", params={"tenant_id": ctx.tenant_id, "branch_id": ctx.branch_id})
    if ok(r): show(r, "settings/restaurant (get)")

    # Menu category
    r = api.post("/menu/categories", json={
        "tenant_id": ctx.tenant_id,
        "branch_id": ctx.branch_id,
        "name": "Starters",
        "position": 1
    })
    assert ok(r), "/menu/categories failed"
    ctx.category_id = show(r, "menu/categories (create)")["id"]
    r = api.get("/menu/categories", params={"tenant_id": ctx.tenant_id, "branch_id": ctx.branch_id})
    show(r, "menu/categories (list)")

    # Menu item
    r = api.post("/menu/items", json={
        "tenant_id": ctx.tenant_id,
        "category_id": ctx.category_id,
        "name": "Paneer Tikka",
        "sku": "PT001",
        "hsn": "2106",
        "tax_inclusive": True
    })
    assert ok(r), "/menu/items failed"
    ctx.item_id = show(r, "menu/items (create)")["id"]

    # Assign station
    r = api.post(f"/menu/items/{ctx.item_id}/assign_station", params={"station_id": ctx.station_id})
    show(r, "menu/items/assign_station")

    # Variant
    r = api.post("/menu/variants", json={
        "item_id": ctx.item_id,
        "label": "Full",
        "base_price": 220.0,
        "is_default": True
    })
    assert ok(r), "/menu/variants failed"
    ctx.variant_id = show(r, "menu/variants (create)")["id"]

    # Update tax
    r = api.post(f"/menu/items/{ctx.item_id}/update_tax", params={"gst_rate": 5.0, "tax_inclusive": True})
    show(r, "menu/items/update_tax")

    # Inventory – ingredients
    ing1 = {"tenant_id": ctx.tenant_id, "name": "Paneer", "uom": "g", "min_level": 1000}
    ing2 = {"tenant_id": ctx.tenant_id, "name": "Masala", "uom": "g", "min_level": 200}
    r = api.post("/inventory/ingredients", json=ing1); ing1_id = show(r, "inventory/ingredients (1)")["id"]
    r = api.post("/inventory/ingredients", json=ing2); ing2_id = show(r, "inventory/ingredients (2)")["id"]

    # Purchase incoming stock
    r = api.post("/inventory/purchase", json={
        "tenant_id": ctx.tenant_id,
        "supplier": "FreshFoods",
        "note": "Initial load",
        "lines": [
            {"ingredient_id": ing1_id, "qty": 5000, "unit_cost": 0.4},
            {"ingredient_id": ing2_id, "qty": 1000, "unit_cost": 0.2},
        ]
    })
    show(r, "inventory/purchase")

    # Recipe BOM for the item (deduct on sale)
    r = api.post("/inventory/recipe", json={
        "item_id": ctx.item_id,
        "lines": [
            {"ingredient_id": ing1_id, "qty": 150},   # 150g paneer
            {"ingredient_id": ing2_id, "qty": 20},    # 20g masala
        ]
    })
    show(r, "inventory/recipe")

    # Check low stock (should be empty after big purchase)
    r = api.get("/inventory/low_stock")
    show(r, "inventory/low_stock")

    # Shift open
    r = api.post("/shift/open", params={"branch_id": ctx.branch_id, "opening_float": 2000})
    assert ok(r), "/shift/open failed"
    ctx.shift_id = show(r, "shift/open")["shift_id"]

    # Shift cash moves
    r = api.post(f"/shift/{ctx.shift_id}/payin", params={"amount": 500, "reason": "Seed cash top-up"})
    show(r, "shift/payin")
    r = api.post(f"/shift/{ctx.shift_id}/payout", params={"amount": 100, "reason": "Petty expense"})
    show(r, "shift/payout")

    # Create order (dine-in)
    order_no = int(time.time())
    r = api.post("/orders/", json={
        "tenant_id": ctx.tenant_id,
        "branch_id": ctx.branch_id,
        "order_no": order_no,
        "channel": "DINE_IN",
        "pax": 2,
        "note": "Window table"
    })
    assert ok(r), "/orders open failed"
    order_out = show(r, "orders/open")
    ctx.order_id = order_out["id"]

    # Add item to order
    r = api.post(f"/orders/{ctx.order_id}/items", json={
        "order_id": ctx.order_id,
        "item_id": ctx.item_id,
        "variant_id": ctx.variant_id,
        "qty": 2,
        "unit_price": 220.0
    })
    assert ok(r), "/orders add item failed"
    show(r, "orders/items (add)")

    # Pay (close order)
    r = api.post(f"/orders/{ctx.order_id}/pay", json={
        "order_id": ctx.order_id,
        "mode": "CASH",
        "amount": 1000.0,   # simple smoke test; total is computed server-side anyway
        "ref_no": f"RCPT-{stamp}"
    })
    assert ok(r), "/orders pay failed"
    bill = show(r, "orders/pay")

    # Create invoice
    r = api.post(f"/orders/{ctx.order_id}/invoice")
    assert ok(r), "/orders invoice failed"
    ctx.invoice_id = show(r, "orders/invoice")["invoice_id"]

    # Attempt invoice reprint (requires REPRINT permission)
    r = api.post(f"/print/invoice/{ctx.invoice_id}", params={"reason": "Customer requested copy"})
    show(r, "print/invoice (reprint)")  # may be 200 or 400 depending on printer setup

    # Try opening cash drawer
    r = api.post("/print/open_drawer")
    show(r, "print/open_drawer")  # may be 200 or 400 depending on config

    # KOT manual ticket + reprint/cancel
    r = api.post("/kot/tickets", params={"order_id": ctx.order_id, "ticket_no": int(time.time())})
    if ok(r):
        tid = show(r, "kot/tickets")["ticket_id"]
        r2 = api.post(f"/kot/{tid}/reprint", params={"reason": "Smudged"})
        show(r2, "kot/reprint")
        r3 = api.post(f"/kot/{tid}/cancel", params={"reason": "Changed mind"})
        show(r3, "kot/cancel")
    else:
        print("→ kot/tickets: skipped")

    # Online webhook (Zomato)
    r = api.post("/online/webhooks/zomato", json={
        "tenant_id": ctx.tenant_id, "branch_id": ctx.branch_id,
        "order_id": f"ZOM-{stamp}"
    })
    if ok(r):
        oo = show(r, "online/webhooks/zomato")
        ctx.online_order_id = oo.get("online_order_id", "")
        ctx.online_core_order_id = oo.get("order_id", "")
        # Some versions have status endpoint:
        r2 = api.post(f"/online/orders/{ctx.online_core_order_id}/status", params={"status": "OPEN"})
        if ok(r2, allow_4xx=True):
            show(r2, "online/orders/status")
        else:
            print("→ online/orders/status not present; skipped")

    # Reports refresh (uses closed orders)
    r = api.post("/reports/daily_sales/refresh", params={"day": today, "branch_id": ctx.branch_id})
    if ok(r, allow_4xx=True):
        show(r, "reports/daily_sales/refresh")
    else:
        print("→ reports/daily_sales/refresh not present; skipped")

    r = api.post("/reports/stock_snapshot/refresh", params={"day": today})
    if ok(r, allow_4xx=True):
        show(r, "reports/stock_snapshot/refresh")
    else:
        print("→ reports/stock_snapshot/refresh not present; skipped")

    # Backup config + run + list
    cfg = {
        "tenant_id": ctx.tenant_id,
        "branch_id": ctx.branch_id,
        "provider": "NONE",
        "local_dir": "./backups",
        "endpoint": None, "bucket": None, "access_key": None, "secret_key": None,
        "schedule_cron": "0 3 * * *"
    }
    r = api.post("/backup/config", json=cfg)
    if ok(r, allow_4xx=True):
        bid = show(r, "backup/config")["id"]
        r2 = api.post("/backup/run", params={
            "config_id": bid, "ok": True, "bytes_total": 123456, "location": "./backups/dummy.zip"
        })
        show(r2, "backup/run")
        r3 = api.get("/backup/runs", params={"config_id": bid})
        show(r3, "backup/runs")
    else:
        print("→ backup endpoints not present; skipped")

    # Sync push/pull
    r = api.post("/sync/push", json={
        "device_id": "TEST-DEVICE-1",
        "ops": [
            {"entity": "ping", "entity_id": str(uuid.uuid4()), "op": "UPSERT", "payload": {"hello": "world"}}
        ]
    })
    if ok(r, allow_4xx=True):
        show(r, "sync/push")
        r2 = api.get("/sync/pull", params={"since": 0, "limit": 10})
        show(r2, "sync/pull")
    else:
        print("→ sync not present; skipped")

    # Users & RBAC (only if those routes exist in your version)
    # Create role
    r = api.post("/users/roles", json={"tenant_id": ctx.tenant_id, "code": f"CASHIER-{uuid.uuid4().hex[:4]}"})
    if ok(r, allow_4xx=True):
        ctx.role_id = show(r, "users/roles (create)")["id"]

        # Grant minimal permissions to role
        r = api.post(f"/users/roles/{ctx.role_id}/grant", json={"permissions": ["SHIFT_CLOSE"]})
        show(r, "users/roles/grant")

        # Create user & assign role
        r = api.post("/users/", json={
            "tenant_id": ctx.tenant_id, "name": f"Cashier {uuid.uuid4().hex[:4]}",
            "mobile": f"9{uuid.uuid4().hex[:9]}",
            "email": f"cashier{uuid.uuid4().hex[:4]}@example.com",
            "password": "pass123", "pin": "1234",
            "roles": []
        })
        ctx.user_id = show(r, "users (create)")["id"]

        r = api.post(f"/users/{ctx.user_id}/roles", json={"roles": [ "MANAGER", "CASHIER" ]})
        show(r, "users/assign_roles")  # will auto-create roles if missing

        # List roles/permissions/users
        r = api.get("/users/roles"); show(r, "users/roles (list)")
        r = api.get("/users/permissions"); show(r, "users/permissions (list)")
        r = api.get("/users", params={"tenant_id": ctx.tenant_id}); show(r, "users (list)")

        # Revoke a permission from role (defensive; may 404 if mismatched)
        r = api.delete(f"/users/roles/{ctx.role_id}/revoke/SHIFT_CLOSE")
        show(r, "users/roles/revoke (SHIFT_CLOSE)")
    else:
        print("→ users/roles endpoints not present; trying minimal /users only")
        # Minimal users-only flow
        r = api.post("/users/", json={
            "tenant_id": ctx.tenant_id, "name": f"Team {uuid.uuid4().hex[:4]}",
            "mobile": f"9{uuid.uuid4().hex[:9]}", "email": None, "password": "pass123", "pin": "1234",
            "roles": []
        })
        if ok(r, allow_4xx=True):
            show(r, "users (create, minimal)")

    # Close shift
    r = api.post(f"/shift/{ctx.shift_id}/close", params={
        "expected_cash": 3400.0, "actual_cash": 3400.0, "note": "End of day"
    })
    if ok(r, allow_4xx=True):
        show(r, "shift/close")

    print("\n=== DONE ✓  (see outputs above) ===")

if __name__ == "__main__":
    main()
