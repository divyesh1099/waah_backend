
import sys
import httpx
import asyncio

URL = "http://localhost:8001"

async def check():
    print(f"Checking {URL}...")
    async with httpx.AsyncClient() as client:
        try:
            # 1. Health/Home
            resp = await client.get(f"{URL}/")
            print(f"ROOT: {resp.status_code}")

            # 2. Categories
            # Needs auth? The router says: Depends(require_auth) or Depends(get_db) depending on endpoint.
            # list_categories takes (tenant_id, branch_id, db, ctx).
            # If it requires auth, we might get 401. 
            # The current implementation of list_categories uses Depends(require_auth).
            # We'll try without token first to see if it's 401 or 404 or connection error.
            print("Checking /menu/categories...")
            resp = await client.get(f"{URL}/menu/categories")
            print(f"CATEGORIES: {resp.status_code}")
            if resp.status_code == 200:
                print(resp.json())
            elif resp.status_code == 401:
                print("Auth required. This is expected if 'require_auth' is enforced.")
                # We can't easily get a token without a user login flow here, 
                # but 401 means the server is UP and the endpoint exists.
            
            # 3. Items
            print("Checking /menu/items...")
            resp = await client.get(f"{URL}/menu/items")
            print(f"ITEMS: {resp.status_code}")
            
        except httpx.ConnectError:
            print("❌ Could not connect to localhost:8000. Is the server running?")
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(check())
