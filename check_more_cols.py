
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def check_more():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'user';")
                cols = {row[0] for row in cur.fetchall()}
                
                required = ["tenant_id", "branch_id", "name", "username", "mobile", "email", "pass_hash", "pin_hash", "active", "created_at", "updated_at", "id"]
                
                print("Missing columns:")
                for r in required:
                    if r not in cols:
                        print(f"MISSING: {r}")
                    else:
                        print(f"FOUND: {r}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_more()
