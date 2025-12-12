import asyncio
import os
import sys

# Ensure we can import app modules
sys.path.append(os.getcwd())

from app.util import r2_client
from app.config import settings

async def main():
    print("--- R2 Configuration ---")
    print(f"Account ID: '{settings.R2_ACCOUNT_ID}'")
    print(f"Bucket:     '{settings.R2_BUCKET_NAME}'")
    print(f"Enabled:    {r2_client.is_enabled()}")
    print("------------------------")

    if not r2_client.is_enabled():
        print("R2 is NOT enabled provided env vars.")
        return

    print("\nRunning health check...")
    try:
        result = await r2_client.check_health()
        print("\n--- Result ---")
        print(result)
        
        if result.get("ok"):
            print("\n✅ SUCCESS: Connected to R2 bucket successfully.")
            
            # Additional Check: Public Reachability
            import httpx
            public_base = result.get("public_base_url")
            if public_base:
                test_url = f"{public_base.rstrip('/')}/r2_health_check_test.txt"
                print(f"\n🌍 Checking public URL: {test_url}")
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(test_url, timeout=5.0)
                        if resp.status_code == 200:
                            print(f"✅ Public Access OK: {resp.text}")
                        else:
                            print(f"❌ Public Access Failed: HTTP {resp.status_code}")
                            print("   (Did you enable 'Public Access' or 'r2.dev' subdomain in Cloudflare?)")
                except Exception as ex:
                    print(f"❌ Public Access Error: {ex}")
            else:
                print("\n⚠️ R2_PUBLIC_BASE_URL is not set. Images will use the private R2 API URL (likely broken for frontend users).")
        else:
            print(f"\n❌ FAILED: {result.get('error')}")
            
    except Exception as e:
        print(f"\n❌ EXCEPTION during check: {e}")

if __name__ == "__main__":
    asyncio.run(main())
