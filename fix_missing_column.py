import sys
import os
sys.path.append(os.getcwd())

from app.db import engine
import psycopg

def fix_schema():
    url = engine.url
    # Construct dsn
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    print(f"Connecting to {url.host}:{url.port}/{url.database}...")
    
    try:
        # Try with sslmode=prefer (default) or disable
        # Using **kwargs style
        with psycopg.connect(conn_str, sslmode="disable") as conn:
            with conn.cursor() as cur:
                print("Checking user.branch_id...")
                cur.execute('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS branch_id VARCHAR(36)')
                print("Added branch_id to user")
                
                # Commit changes
                conn.commit()
                print("Done.")
    except Exception as e:
        print(f"Connection failed with sslmode=disable: {e}")
        # Could try other modes if needed

if __name__ == "__main__":
    try:
        fix_schema()
    except Exception as e:
        print(f"Top Level Error: {e}")
