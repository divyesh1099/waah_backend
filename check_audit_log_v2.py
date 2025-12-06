
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def audit():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    expected = ["id", "actor_user_id", "entity", "entity_id", "action", "reason", "before", "after"]

    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_log';")
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
