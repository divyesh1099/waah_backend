
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def audit():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    expected = [
        "id", "tenant_id", "branch_id", "order_no", "channel", 
        "provider", "status", "table_id", "customer_id", 
        "opened_by_user_id", "closed_by_user_id", "pax", 
        "source_device_id", "note", "opened_at", "closed_at"
    ]

    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = 'order';")
                actual = {row[0] for row in cur.fetchall()}
                
                print(f"Columns found: {len(actual)}")
                missing = []
                for c in expected:
                    if c not in actual:
                        missing.append(c)
                
                if missing:
                    print(f"MISSING columns: {missing}")
                else:
                    print("All expected columns found.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    audit()
