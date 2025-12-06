
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def audit():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    expected = ["id", "actor_user_id", "entity", "entity_id", "action", "reason", "before", "after", "created_at"]

    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                print("Checking audit_log...")
                try:
                    cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_log';")
                    actual = {row[0] for row in cur.fetchall()}
                except Exception as e:
                    print(f"Failed to query info schema: {e}")
                    return

                if not actual:
                    print("Table audit_log does not exist (or has no columns)")
                
                print(f"Actual columns: {actual}")
                for c in expected:
                    if c not in actual:
                        print(f"MISSING in audit_log: {c}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    audit()
