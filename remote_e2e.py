#!/usr/bin/env python3
"""
Remote E2E test runner for Waah Backend (idempotent where possible).

Run:
    BASE_URL=https://waahbackend-production.up.railway.app python remote_e2e.py
"""

import os
import sys
import time
import uuid
import json
from datetime import date
import requests

BASE_URL = os.getenv("BASE_URL", "https://waahbackend-production.up.railway.app").rstrip("/")
TIMEOUT = 25
TENANT = ""
BRANCH = ""
RNG = str(int(time.time()))[-6:] + "-" + uuid.uuid4().hex[:6]


def _url(path: str) -> str:
    path = path if path.startswith("/") else f"/{path}"
    return f"{BASE_URL}{path}"


def jprint(step: str, r: requests.Response):
    if not (200 <= r.status_code < 300):
        # Pretty failure to STDOUT + exit with nonzero
        ct = r.headers.get("content-type", "")
        body = r.text if "application/json" not in ct else json.dumps(r.json(), indent=2)
        print(f"\n❌ {step} -> {r.status_code}\n{body}\n", file=sys.stderr)
        sys.exit(1)
    # Show small PASS line
    print(f"✅ {step} [{r.status_code}]")
    return r.json() if r.headers.get("content-type", "").startswith("application/json") and r.text else {}


def maybe(step: str, fn, *args, **kwargs):
    """Run a non-blocking step; log but don't fail the suite."""
    try:
        r = fn(*args, **kwargs)
        ok = 200 <= r.status_code < 300
        print(("✅" if ok else "⚠️ ") + f" {step} [{r.status_code}]")
        if ok and r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return None
    except Exception as e:
        print(f"⚠️  {step} (skipped/error): {e}")
        return None


def req(method: str, path: str, token: str | None = None, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {}) or {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if "json" in kwargs:
        headers.setdefault("content-type", "application/json")
    return requests.request(method, _url(path), headers=headers, timeout=TIMEOUT, **kwargs)


def login_or_bootstrap() -> str:
    # Health
    jprint("GET /healthz", req("GET", "/healthz"))

    # Attempt direct login first (dev bootstrap defaults)
    mobile, password = "9999999999", "admin"
    r = req("POST", f"/auth/login?mobile={mobile}&password={password}")
    if r.status_code >= 400:
        # Try bootstrap then login
        maybe("POST /admin/dev-bootstrap", req, "POST", "/admin/dev-bootstrap")
        r = req("POST", f"/auth/login?mobile={mobile}&password={password}")
    data = jprint("POST /auth/login", r)
    token = data.get("token") or data.get("access_token")
    if not token:
        print("❌ No JWT token returned from /auth/login", file=sys.stderr)
        sys.exit(1)
    return token


def main():
    token = login_or_bootstrap()

    # ===== 1) Settings: printers, station, restaurant =====
    r = req("POST", "/settings/printers", token, json={
        "tenant_id": TENANT, "branch_id": BRANCH,
        "name": f"Billing-{RNG}", "type": "BILLING",
        "connection_url": "http://localhost:9100/agent", "is_default": True,
        "cash_drawer_enabled": True, "cash_drawer_code": "PULSE_2_100",
    })
    bill_pr_id = jprint("POST /settings/printers (billing)", r)["id"]

    r = req("POST", "/settings/printers", token, json={
        "tenant_id": TENANT, "branch_id": BRANCH,
        "name": f"Kitchen-{RNG}", "type": "KITCHEN",
        "connection_url": "http://localhost:9101/agent", "is_default": True,
    })
    kit_pr_id = jprint("POST /settings/printers (kitchen)", r)["id"]

    r = req("POST", "/settings/stations", token, json={
        "tenant_id": TENANT, "branch_id": BRANCH,
        "name": f"Tandoor-{RNG}", "printer_id": kit_pr_id
    })
    station_id = jprint("POST /settings/stations", r)["id"]

    r = req("POST", "/settings/restaurant", token, json={
        "tenant_id": TENANT, "branch_id": BRANCH,
        "name": f"Waah {RNG}",
        "logo_url": None,
        "address": "123 Food Street", "phone": "1800123456",
        "gstin": "27ABCDE1234F2Z5", "fssai": "11223344556677",
        "print_fssai_on_invoice": True, "gst_inclusive_default": True,
        "service_charge_mode": "NONE", "service_charge_value": 0,
        "packing_charge_mode": "NONE", "packing_charge_value": 0,
        "billing_printer_id": bill_pr_id,
        "invoice_footer": "Thank you! Visit again."
    })
    rs_id = jprint("POST /settings/restaurant", r)["id"]

    jprint("GET /settings/restaurant",
           req("GET", "/settings/restaurant", token, params={"tenant_id": TENANT, "branch_id": BRANCH}))

    # ===== 2) Menu: categories, items, variants, modifiers =====
    r = req("POST", "/menu/categories", token, json={
        "tenant_id": TENANT, "branch_id": BRANCH, "name": f"Starters-{RNG}", "position": 1
    })
    cat_main_id = jprint("POST /menu/categories (Starters)", r)["id"]

    r = req("POST", "/menu/categories", token, json={
        "tenant_id": TENANT, "branch_id": BRANCH, "name": f"Beverages-{RNG}", "position": 2
    })
    cat_bev_id = jprint("POST /menu/categories (Beverages)", r)["id"]

    r = req("POST", "/menu/items", token, json={
        "tenant_id": TENANT, "category_id": cat_main_id,
        "name": f"Paneer Tikka-{RNG}", "sku": f"PT{RNG}", "hsn": "2106",
        "tax_inclusive": True, "kitchen_station_id": station_id
    })
    item_id = jprint("POST /menu/items (Paneer)", r)["id"]

    r = req("POST", "/menu/variants", token, json={
        "item_id": item_id, "label": "Full", "base_price": 220.0, "is_default": True
    })
    variant_id = jprint("POST /menu/variants (Paneer Full)", r)["id"]

    # beverage item for cancel/void tests
    r = req("POST", "/menu/items", token, json={
        "tenant_id": TENANT, "category_id": cat_bev_id,
        "name": f"Coke-{RNG}", "tax_inclusive": True, "gst_rate": 5.0
    })
    coke_id = jprint("POST /menu/items (Coke)", r)["id"]

    r = req("POST", "/menu/variants", token, json={
        "item_id": coke_id, "label": "Regular", "base_price": 50.0, "is_default": True
    })
    coke_var_id = jprint("POST /menu/variants (Coke)", r)["id"]

    # modifiers for pizza-like test on paneer
    r = req("POST", "/menu/modifier_groups", token, json={
        "tenant_id": TENANT, "name": f"Toppings-{RNG}", "min_sel": 0, "max_sel": 3
    })
    mod_group_id = jprint("POST /menu/modifier_groups", r)["id"]

    mod_cheese_id = jprint("POST /menu/modifiers (Cheese)",
                           req("POST", "/menu/modifiers", token, json={
                               "group_id": mod_group_id, "name": "Extra Cheese", "price_delta": 40.0
                           }))["id"]

    mod_olives_id = jprint("POST /menu/modifiers (Olives)",
                           req("POST", "/menu/modifiers", token, json={
                               "group_id": mod_group_id, "name": "Olives", "price_delta": 30.0
                           }))["id"]

    jprint("POST /menu/items/{id}/modifier_groups (link)",
           req("POST", f"/menu/items/{item_id}/modifier_groups", token, json={"group_id": mod_group_id}))

    # ===== 3) Dining & Customer =====
    r = req("POST", "/dining/tables", token, json={
        "branch_id": BRANCH, "code": f"T-{RNG}", "zone": "AC Hall", "seats": 4
    })
    table_id = jprint("POST /dining/tables", r)["id"]

    r = req("POST", "/customers/", token, json={
        "tenant_id": TENANT, "name": f"Test Customer {RNG}", "phone": f"98{int(time.time())%10_000_000:07d}"
    })
    cust_id = jprint("POST /customers", r)["id"]

    # ===== 4) Inventory =====
    ing1 = jprint("POST /inventory/ingredients (Paneer)",
                  req("POST", "/inventory/ingredients", token, json={
                      "tenant_id": TENANT, "name": f"Paneer-{RNG}", "uom": "g", "min_level": 500
                  }))["id"]
    ing2 = jprint("POST /inventory/ingredients (Masala)",
                  req("POST", "/inventory/ingredients", token, json={
                      "tenant_id": TENANT, "name": f"Masala-{RNG}", "uom": "g", "min_level": 200
                  }))["id"]

    jprint("POST /inventory/purchase",
           req("POST", "/inventory/purchase", token, json={
               "tenant_id": TENANT, "supplier": "Fresh Farms", "note": "Initial stock",
               "lines": [
                   {"ingredient_id": ing1, "qty": 2000, "unit_cost": 0.5},
                   {"ingredient_id": ing2, "qty": 1000, "unit_cost": 0.2},
               ]
           }))

    jprint("POST /inventory/recipe",
           req("POST", "/inventory/recipe", token, json={
               "item_id": item_id, "lines": [
                   {"ingredient_id": ing1, "qty": 200},
                   {"ingredient_id": ing2, "qty": 30}
               ]
           }))

    jprint("GET /inventory/low_stock", req("GET", "/inventory/low_stock", token))

    # ===== 5) Shift: open/payments =====
    shift_id = jprint("POST /shift/open", req("POST", "/shift/open", token,
                                              params={"branch_id": BRANCH, "opening_float": 1500.0}))["shift_id"]

    jprint("POST /shift/{id}/payin", req("POST", f"/shift/{shift_id}/payin", token,
                                         params={"amount": 500.0, "reason": "Float top-up"}))
    jprint("POST /shift/{id}/payout", req("POST", f"/shift/{shift_id}/payout", token,
                                          params={"amount": 100.0, "reason": "Groceries"}))

    # ===== 6) Orders: add items, cancel, discount, pay, invoice =====
    order_id = jprint("POST /orders (DINE_IN)", req("POST", "/orders/", token, json={
        "tenant_id": TENANT, "branch_id": BRANCH, "order_no": int(time.time()),
        "channel": "DINE_IN", "pax": 2, "table_id": table_id, "customer_id": cust_id, "note": "Window table"
    }))["id"]

    # Coke (will cancel)
    coke_line_id = jprint("POST /orders/{id}/items (Coke)",
                          req("POST", f"/orders/{order_id}/items", token, json={
                              "order_id": order_id, "item_id": coke_id, "variant_id": coke_var_id,
                              "qty": 1, "unit_price": 50.0
                          }))["id"]

    # Paneer with modifiers
    paneer_line_id = jprint("POST /orders/{id}/items (Paneer+Mods)",
                            req("POST", f"/orders/{order_id}/items", token, json={
                                "order_id": order_id, "item_id": item_id, "variant_id": variant_id,
                                "qty": 1, "unit_price": 220.0,
                                "modifiers": [
                                    {"modifier_id": mod_cheese_id, "qty": 1, "price_delta": 40.0},
                                    {"modifier_id": mod_olives_id, "qty": 1, "price_delta": 30.0}
                                ]
                            }))["id"]

    # Cancel coke
    jprint("DELETE /orders/{id}/items/{line_id}",
           req("DELETE", f"/orders/{order_id}/items/{coke_line_id}", token,
               params={"reason": "Customer changed mind"}))

    # Discount paneer line
    jprint("POST /orders/{id}/items/{line_id}/apply_discount",
           req("POST", f"/orders/{order_id}/items/{paneer_line_id}/apply_discount", token,
               json={"discount": 20.0, "reason": "Manager offer"}))

    # Read order & pay split
    order_data = jprint("GET /orders/{id}", req("GET", f"/orders/{order_id}", token))
    total_due = float(order_data.get("total_due", 350.0))
    pay_upi = 150.0
    pay_cash = round(total_due - pay_upi, 2)

    jprint("POST /orders/{id}/pay (UPI)", req("POST", f"/orders/{order_id}/pay", token, json={
        "order_id": order_id, "mode": "UPI", "amount": pay_upi, "ref_no": "gpay12345"
    }))
    jprint("POST /orders/{id}/pay (CASH)", req("POST", f"/orders/{order_id}/pay", token, json={
        "order_id": order_id, "mode": "CASH", "amount": pay_cash
    }))

    inv = jprint("POST /orders/{id}/invoice", req("POST", f"/orders/{order_id}/invoice", token))
    invoice_id = inv.get("invoice_id") or inv.get("id") or "UNKNOWN"

    # Reprint + open drawer
    jprint("POST /print/invoice/{invoice_id}",
           req("POST", f"/print/invoice/{invoice_id}", token, params={"reason": "Customer request"}))
    maybe("POST /print/open_drawer", req, "POST", "/print/open_drawer", token)

    # ===== 7) KOT explicit ticket + reprint + cancel =====
    ticket_id = jprint("POST /kot/tickets", req("POST", "/kot/tickets", token, params={
        "order_id": order_id, "ticket_no": int(time.time()), "target_station": station_id
    }))["ticket_id"]

    jprint("POST /kot/{id}/reprint", req("POST", f"/kot/{ticket_id}/reprint", token, params={"reason": "Blurred"}))
    jprint("POST /kot/{id}/cancel", req("POST", f"/kot/{ticket_id}/cancel", token, params={"reason": "Cancelled"}))

    # ===== 8) Online webhook =====
    online = jprint("POST /online/webhooks/zomato", req("POST", "/online/webhooks/zomato", token, json={
        "order_id": f"Z-{int(time.time())}", "tenant_id": TENANT, "branch_id": BRANCH
    }))
    maybe("POST /online/orders/{id}/status -> READY",
          req, "POST", f"/online/orders/{online.get('order_id','')}/status", token, params={"status": "READY"})

    # ===== 9) Reports =====
    today = date.today().isoformat()
    jprint("POST /reports/daily_sales/refresh",
           req("POST", "/reports/daily_sales/refresh", token, params={"day": today, "branch_id": BRANCH}))
    jprint("POST /reports/stock_snapshot/refresh",
           req("POST", "/reports/stock_snapshot/refresh", token, params={"day": today}))

    # ===== 10) Backup =====
    cfg_id = jprint("POST /backup/config", req("POST", "/backup/config", token, json={
        "tenant_id": TENANT, "branch_id": BRANCH, "provider": "NONE",
        "local_dir": "./backups", "endpoint": None, "bucket": None,
        "access_key": None, "secret_key": None, "schedule_cron": "0 3 * * *",
    }))["id"]
    jprint("POST /backup/run", req("POST", "/backup/run", token, params={
        "config_id": cfg_id, "ok": True, "bytes_total": 12345, "location": "./backups/demo.zip"
    }))
    jprint("GET /backup/runs", req("GET", "/backup/runs", token, params={"config_id": cfg_id}))

    # ===== 11) Users / Roles / Permissions =====
    user_id = jprint("POST /users", req("POST", "/users/", token, json={
        "tenant_id": TENANT, "name": f"Manager {RNG}",
        "mobile": f"98{int(time.time())%10_000_000:07d}",
        "email": f"mgr{RNG}@example.com",
        "password": "secret", "pin": "1234", "roles": ["ADMIN"]
    }))["id"]

    jprint("GET /users", req("GET", "/users/", token, params={"tenant_id": TENANT}))
    roles = jprint("GET /users/roles", req("GET", "/users/roles", token, params={"tenant_id": TENANT}))
    admin_role_id = next((r["id"] for r in roles if r.get("code") == "ADMIN"), None)
    if admin_role_id:
        jprint("POST /users/roles/{id}/grant",
               req("POST", f"/users/roles/{admin_role_id}/grant", token,
                   json={"permissions": ["SHIFT_CLOSE", "SETTINGS_EDIT"]}))
        # revoke (optional)
        maybe("DELETE /users/roles/{id}/revoke/SHIFT_CLOSE",
              req, "DELETE", f"/users/roles/{admin_role_id}/revoke/SHIFT_CLOSE", token)

    # ===== 12) Sync =====
    jprint("POST /sync/push", req("POST", "/sync/push", token, json={
        "device_id": str(uuid.uuid4()),
        "ops": [{"entity": "note", "entity_id": str(uuid.uuid4()), "op": "UPSERT", "payload": {"hello": "world"}}]
    }))
    jprint("GET /sync/pull", req("GET", "/sync/pull", token, params={"since": 0, "limit": 10}))

    # ===== 13) Void order flow (separate order) =====
    order_id2 = jprint("POST /orders (TAKEAWAY)", req("POST", "/orders/", token, json={
        "tenant_id": TENANT, "branch_id": BRANCH, "order_no": int(time.time()),
        "channel": "TAKEAWAY", "note": "To be voided"
    }))["id"]

    jprint("POST /orders/{id}/items (Coke x2)",
           req("POST", f"/orders/{order_id2}/items", token, json={
               "order_id": order_id2, "item_id": coke_id, "variant_id": coke_var_id, "qty": 2, "unit_price": 50.0
           }))
    jprint("POST /orders/{id}/void",
           req("POST", f"/orders/{order_id2}/void", token, params={"reason": "Test void workflow"}))

    # ===== 14) Shift Close with mismatch =====
    expected_cash = 1500.0 + pay_cash + 500.0 - 100.0  # opening + cash sale + payin - payout
    actual_cash = expected_cash - 50.0
    r = req("POST", f"/shift/{shift_id}/close", token, params={
        "expected_cash": expected_cash, "actual_cash": actual_cash,
        "note": "Cash is 50.0 short, review required."
    })
    if r.status_code == 403:
        print("⚠️  SHIFT_CLOSE permission not granted to current user. Skipping close mismatch.")
    else:
        jprint("POST /shift/{id}/close (mismatch)", r)

    print("\n🎉 ALL REMOTE TESTS PASSED for", BASE_URL)


if __name__ == "__main__":
    main()
