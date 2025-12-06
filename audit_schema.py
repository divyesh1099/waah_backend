
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def audit():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    expected = {
        "tenant": ["id", "created_at", "updated_at", "deleted_at", "version", "name"],
        "user": ["id", "created_at", "updated_at", "deleted_at", "version", "tenant_id", "branch_id", "name", "username", "mobile", "email", "pass_hash", "pin_hash", "active"],
        "role": ["id", "created_at", "updated_at", "deleted_at", "version", "tenant_id", "code"],
        "permission": ["id", "created_at", "updated_at", "deleted_at", "version", "code", "description"],
        "onboard_progress": ["id", "created_at", "updated_at", "deleted_at", "version", "tenant_id", "completed", "step", "last_note"]
    }

    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                for table, cols in expected.items():
                    print(f"Checking {table}...")
                    cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}';")
                    actual = {row[0] for row in cur.fetchall()}
                    
                    for c in cols:
                        if c not in actual:
                            print(f"MISSING in {table}: {c}")
                        else:
                             pass # print(f"OK: {table}.{c}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    audit()
