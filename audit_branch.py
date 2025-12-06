
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def audit_branch():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    expected = ["id", "created_at", "updated_at", "deleted_at", "version", "tenant_id", "name", "gstin", "address", "phone", "state_code", "code"]

    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                print("Checking branch...")
                cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = 'branch';")
                actual = {row[0] for row in cur.fetchall()}
                
                print(f"Actual columns: {actual}")
                for c in expected:
                    if c not in actual:
                        print(f"MISSING in branch: {c}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    audit_branch()
