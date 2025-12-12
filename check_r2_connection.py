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
        else:
            print(f"\n❌ FAILED: {result.get('error')}")
            
    except Exception as e:
        print(f"\n❌ EXCEPTION during check: {e}")

if __name__ == "__main__":
    asyncio.run(main())
