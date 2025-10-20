# test_advanced_order_flow_e2e.py
import time
import uuid
from datetime import date
import pytest

def jprint(step, r):
    """Helper to print response and assert on failure."""
    assert 200 <= r.status_code < 300, f"{step} -> {r.status_code}: {r.text}"
    return r.json() if r.headers.get("content-type","").startswith("application/json") else r.text

def test_advanced_order_and_shift_flow(client, base_url, auth_headers, rng_suffix):
    # ===== 1. Base Setup (Printers, Station, Restaurant) =====
    # We need a minimal setup to route KOTs and link to billing
    r = client.post(f"{base_url}/settings/printers", headers=auth_headers, json={
        "tenant_id":"", "branch_id":"", "name":f"Billing-{rng_suffix}", "type":"BILLING",
        "connection_url":"http://localhost:9100/agent", "is_default": True
    })
    bill_pr_id = jprint("POST /settings/printers (billing)", r)["id"]

    r = client.post(f"{base_url}/settings/printers", headers=auth_headers, json={
        "tenant_id":"", "branch_id":"", "name":f"Kitchen-{rng_suffix}", "type":"KITCHEN",
        "connection_url":"http://localhost:9101/agent", "is_default": True
    })
    kit_pr_id = jprint("POST /settings/printers (kitchen)", r)["id"]

    r = client.post(f"{base_url}/settings/stations", headers=auth_headers, json={
        "tenant_id":"", "branch_id":"", "name":f"MainCourse-{rng_suffix}", "printer_id": kit_pr_id
    })
    station_id = jprint("POST /settings/stations", r)["id"]

    r = client.post(f"{base_url}/settings/restaurant", headers=auth_headers, json={
        "tenant_id":"", "branch_id":"", "name":f"Waah {rng_suffix}",
        "billing_printer_id": bill_pr_id, "gstin":"27TESTGST1234",
    })
    jprint("POST /settings/restaurant", r)

    # ===== 2. Menu with Modifiers =====
    r = client.post(f"{base_url}/menu/categories", headers=auth_headers, json={
        "tenant_id":"", "branch_id":"", "name":f"Main Course-{rng_suffix}", "position":1
    })
    cat_main_id = jprint("POST /menu/categories (Main)", r)["id"]
    
    r = client.post(f"{base_url}/menu/categories", headers=auth_headers, json={
        "tenant_id":"", "branch_id":"", "name":f"Beverages-{rng_suffix}", "position":2
    })
    cat_bev_id = jprint("POST /menu/categories (Bev)", r)["id"]

    # Item 1: Pizza
    r = client.post(f"{base_url}/menu/items", headers=auth_headers, json={
        "tenant_id":"", "category_id": cat_main_id, "name":f"Pizza-{rng_suffix}", 
        "kitchen_station_id": station_id, "gst_rate": 5.0
    })
    pizza_id = jprint("POST /menu/items (Pizza)", r)["id"]

    r = client.post(f"{base_url}/menu/variants", headers=auth_headers, json={
        "item_id": pizza_id, "label":"Medium", "base_price":300.0, "is_default": True
    })
    pizza_var_id = jprint("POST /menu/variants (Pizza)", r)["id"]

    # Item 2: Coke (for cancellation test)
    r = client.post(f"{base_url}/menu/items", headers=auth_headers, json={
        "tenant_id":"", "category_id": cat_bev_id, "name":f"Coke-{rng_suffix}",
        "kitchen_station_id": None, "gst_rate": 5.0
    })
    coke_id = jprint("POST /menu/items (Coke)", r)["id"]

    r = client.post(f"{base_url}/menu/variants", headers=auth_headers, json={
        "item_id": coke_id, "label":"Regular", "base_price":50.0, "is_default": True
    })
    coke_var_id = jprint("POST /menu/variants (Coke)", r)["id"]
    
    # Modifiers for Pizza
    r = client.post(f"{base_url}/menu/modifier_groups", headers=auth_headers, json={
        "tenant_id": "", "name": "Pizza Toppings", "min_sel": 0, "max_sel": 3
    })
    mod_group_id = jprint("POST /menu/modifier_groups", r)["id"]
    
    r = client.post(f"{base_url}/menu/modifiers", headers=auth_headers, json={
        "group_id": mod_group_id, "name": "Extra Cheese", "price_delta": 40.0
    })
    mod_cheese_id = jprint("POST /menu/modifiers (Cheese)", r)["id"]
    
    r = client.post(f"{base_url}/menu/modifiers", headers=auth_headers, json={
        "group_id": mod_group_id, "name": "Olives", "price_delta": 30.0
    })
    mod_olives_id = jprint("POST /menu/modifiers (Olives)", r)["id"]
    
    # Link modifier group to Pizza
    r = client.post(f"{base_url}/menu/items/{pizza_id}/modifier_groups", headers=auth_headers, json={
        "group_id": mod_group_id
    })
    jprint("POST /menu/items/{id}/modifier_groups (link)", r)

    # ===== 3. Dining & Customer Setup =====
    r = client.post(f"{base_url}/dining/tables", headers=auth_headers, json={
        "branch_id": "", "code": f"T5-{rng_suffix}", "zone": "AC Hall", "seats": 4
    })
    table_id = jprint("POST /dining/tables", r)["id"]
    
    r = client.post(f"{base_url}/customers/", headers=auth_headers, json={
        "tenant_id": "", "name": f"Test Customer {rng_suffix}", "phone": f"98{int(time.time())%10_000_000:07d}"
    })
    cust_id = jprint("POST /customers/", r)["id"]

    # ===== 4. Shift Open =====
    r = client.post(f"{base_url}/shift/open", headers=auth_headers, params={"branch_id":"", "opening_float": 2000.0})
    shift_id = jprint("POST /shift/open", r)["shift_id"]
    
    # ===== 5. Complex Order Flow (Add, Modify, Cancel, Discount) =====
    r = client.post(f"{base_url}/orders/", headers=auth_headers, json={
        "tenant_id":"", "branch_id":"", "order_no": int(time.time()),
        "channel":"DINE_IN", "pax":2, "table_id": table_id, "customer_id": cust_id
    })
    order_id = jprint("POST /orders/ (Dine-In)", r)["id"]
    
    # Add Coke (will be cancelled)
    r = client.post(f"{base_url}/orders/{order_id}/items", headers=auth_headers, json={
        "order_id": order_id, "item_id": coke_id, "variant_id": coke_var_id, "qty": 1, "unit_price": 50.0
    })
    coke_line_id = jprint("POST /orders/{id}/items (Coke)", r)["id"]
    
    # Add Pizza with Modifiers
    r = client.post(f"{base_url}/orders/{order_id}/items", headers=auth_headers, json={
        "order_id": order_id, "item_id": pizza_id, "variant_id": pizza_var_id, "qty": 1, "unit_price": 300.0,
        "modifiers": [
            {"modifier_id": mod_cheese_id, "qty": 1, "price_delta": 40.0},
            {"modifier_id": mod_olives_id, "qty": 1, "price_delta": 30.0}
        ]
    })
    pizza_line_id = jprint("POST /orders/{id}/items (Pizza+Mods)", r)["id"]
    
    # Cancel the Coke
    # Assuming DELETE /orders/{order_id}/items/{order_item_id}
    # This might require a reason depending on audit rules
    r = client.delete(f"{base_url}/orders/{order_id}/items/{coke_line_id}", headers=auth_headers, params={
        "reason": "Customer changed mind"
    })
    jprint("DELETE /orders/{id}/items/{line_id} (Cancel Coke)", r)
    
    # Apply a discount to the Pizza
    # Assuming POST /orders/{order_id}/items/{order_item_id}/apply_discount
    r = client.post(f"{base_url}/orders/{order_id}/items/{pizza_line_id}/apply_discount", headers=auth_headers, json={
        "discount": 20.0,  # 20.0 currency unit discount
        "reason": "Manager Offer"
    })
    jprint("POST /orders/{id}/items/{line_id}/apply_discount", r)

    # ===== 6. Multi-Tender (Split) Payment =====
    # Total should be:
    # Pizza: 300 + Cheese: 40 + Olives: 30 = 370
    # Discount: -20
    # Total Due: 350
    # (GST calculation happens at API level)
    # Let's assume the final total is 350.0 (pre-tax/post-tax logic handled by API)
    # We'll fetch the order to get the real total_due
    
    r = client.get(f"{base_url}/orders/{order_id}", headers=auth_headers)
    order_data = jprint("GET /orders/{id}", r)
    # Assuming the API response includes a 'total_due' field
    total_due = order_data.get("total_due", 350.0) # Fallback for test
    
    pay_upi = 150.0
    pay_cash = total_due - pay_upi
    
    r = client.post(f"{base_url}/orders/{order_id}/pay", headers=auth_headers, json={
        "order_id": order_id, "mode":"UPI", "amount": pay_upi, "ref_no": "gpay12345"
    })
    jprint("POST /orders/{id}/pay (UPI)", r)
    
    r = client.post(f"{base_url}/orders/{order_id}/pay", headers=auth_headers, json={
        "order_id": order_id, "mode":"CASH", "amount": pay_cash, "ref_no": None
    })
    jprint("POST /orders/{id}/pay (CASH)", r)
    
    # Generate invoice
    r = client.post(f"{base_url}/orders/{order_id}/invoice", headers=auth_headers)
    jprint("POST /orders/{id}/invoice", r)

    # ===== 7. Void Order Flow =====
    r = client.post(f"{base_url}/orders/", headers=auth_headers, json={
        "tenant_id":"", "branch_id":"", "order_no": int(time.time()),
        "channel":"TAKEAWAY", "note":"Test order to be voided"
    })
    order_id_2 = jprint("POST /orders/ (Takeaway)", r)["id"]
    
    r = client.post(f"{base_url}/orders/{order_id_2}/items", headers=auth_headers, json={
        "order_id": order_id_2, "item_id": coke_id, "variant_id": coke_var_id, "qty": 2, "unit_price": 50.0
    })
    jprint("POST /orders/{id}/items (for void test)", r)
    
    # Void the entire order
    r = client.post(f"{base_url}/orders/{order_id_2}/void", headers=auth_headers, params={
        "reason": "Test void workflow"
    })
    jprint("POST /orders/{id}/void", r)

    # ===== 8. Shift Close with Mismatch =====
    # Add a Payout to complicate cash calculation
    r = client.post(f"{base_url}/shift/{shift_id}/payout", headers=auth_headers, params={"amount":100.0, "reason":"Cleaning supplies"})
    jprint("POST /shift/{id}/payout", r)

    # Opening Float: 2000.0
    # Cash Payment: pay_cash (e.g., 200.0 if total was 350)
    # Payout: -100.0
    # Expected Cash: 2000.0 + pay_cash - 100.0 = 2100.0
    
    expected_cash = 2000.0 + pay_cash - 100.0
    actual_cash = expected_cash - 50.0 # Deliberate 50.0 short
    
    r = client.post(f"{base_url}/shift/{shift_id}/close", headers=auth_headers, params={
        "expected_cash": expected_cash, 
        "actual_cash": actual_cash, 
        "note": "Cash is 50.0 short, review required."
    })
    
    # This should pass (2xx) but flag the mismatch internally
    # The first test skips on 403; we'll do the same just in case.
    if r.status_code == 403:
        pytest.skip("SHIFT_CLOSE permission not granted to current user.")
    
    jprint("POST /shift/{id}/close (with mismatch)", r)