import sys
import os

sys.path.append(os.getcwd())

from app.db import SessionLocal
from app.models.core import RestaurantSettings

def check_logo():
    db = SessionLocal()
    tenant_id = "bb91d527-8882-4ba5-9800-7494a4cf72ec"
    branch_id = "2938245c-0d5e-4b1f-a443-9d5d2b07624e"
    
    rs = db.query(RestaurantSettings).filter(
        RestaurantSettings.tenant_id == tenant_id,
        RestaurantSettings.branch_id == branch_id
    ).first()
    
    if rs:
        print(f"--- Restaurant Settings (ID: {rs.id}) ---")
        print(f"Tenant: {rs.tenant_id}")
        print(f"Branch: {rs.branch_id}")
        print(f"Logo URL: '{rs.logo_url}'")
        
        if rs.logo_url and "r2.dev" in rs.logo_url:
            print("✅ Status: Using R2 URL.")
        elif rs.logo_url and rs.logo_url.startswith("/media"):
            print("⚠️ Status: Using LOCAL URL (Upload might have failed or not updated DB).")
        else:
            print("❓ Status: Unknown format or empty.")
    else:
        print("❌ Restaurant settings not found for this tenant/branch.")
    
    db.close()

if __name__ == "__main__":
    check_logo()
