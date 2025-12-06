
import sys
import os
import traceback
from datetime import datetime
sys.path.append(os.getcwd())
from app.db import SessionLocal
from app.models.core import User, Tenant, Branch, Order, OrderItem, MenuItem, KitchenStation, OrderStatus, OrderChannel, KOTStatus, KitchenTicket
from app.routers.orders import fire_kot_for_order
from fastapi import BackgroundTasks

import asyncio
from collections import namedtuple

# Mock AuthCtx matching app.deps definitions if possible, or just a named tuple
# In app.deps, AuthCtx is a dataclass or similar. 
# Using namedtuple is usually enough if properties match.
AuthCtx = namedtuple("AuthCtx", ["user_id", "tenant_id", "branch_id"])

async def check_async():
    db = SessionLocal()
    try:
        print("Checking KOT Logic Standalone (Async)...")
        
        # 1. Setup Data
        t = db.query(Tenant).filter(Tenant.name == "KotTestTenant").first()
        b = db.query(Branch).filter(Branch.tenant_id == t.id).first()
        u = db.query(User).filter(User.username == "kot_tester").first()
        if not all([t, b, u]):
            print("Missing test data! Run setup first (or verify_kot_fire.py setup).")
            # Create if missing? (Simplified for now - assumes we ran verify_kot_fire at least once for setup)
            # Actually, verify_kot_fire.py failed setup many times but succeeded creating user/items eventually?
            # Let's assume data exists or verify script fails fast.
            return

        item = db.query(MenuItem).filter(MenuItem.tenant_id == t.id).first()
        if not item:
            print("No menu item found!")
            return
            
        ctx = AuthCtx(user_id=u.id, tenant_id=t.id, branch_id=b.id)

        # Create Order
        order_no = f"KOT-TEST-L-{int(datetime.now().timestamp())}"
        order = Order(
            tenant_id=t.id, branch_id=b.id,
            order_no=order_no,
            channel=OrderChannel.DINE_IN,
            status=OrderStatus.OPEN,
            opened_by_user_id=u.id,
            pax=2
        )
        db.add(order)
        db.flush()
        print(f"Order created: {order.id}")
        
        # Add Item
        oi = OrderItem(
            order_id=order.id,
            item_id=item.id,
            qty=2.0,
            unit_price=150,
            taxable_value=300,
            cgst=7.5, sgst=7.5
        )
        db.add(oi)
        db.commit()
        print(f"Item added: {oi.id}")
        
        # 2. Call fire_kot_for_order
        print("Firing KOT...")
        
        # Signature: async def fire_kot_for_order(order_id: str, db: Session = Depends(get_db), ctx: AuthCtx = Depends(require_auth))
        res = await fire_kot_for_order(order.id, db=db, ctx=ctx)
        print(f"Fire Result: {res}")
        
        # 3. Verify
        db.expire_all() # ensure fresh data
        db.refresh(order)
        print(f"Order Status: {order.status}")
        
        tickets = db.query(KitchenTicket).filter(KitchenTicket.order_id == order.id).all()
        print(f"Tickets created: {len(tickets)}")
        for t in tickets:
            print(f"Ticket {t.ticket_no}: {t.status} (Station: {t.target_station})")
            
        if order.status == OrderStatus.KITCHEN and len(tickets) > 0:
            print("SUCCESS: KOT Logic Verified.")
        else:
             print("FAILURE: Status check failed.")

    except Exception as e:
        print("CRASHED:")
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(check_async())
