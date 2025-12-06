
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def inspect_schema():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    tables_to_check = ["tenant", "user", "role", "permission", "onboard_progress", "branch"]
    
    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                for t in tables_to_check:
                    print(f"--- Table: {t} ---")
                    try:
                        cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{t}';")
                        rows = cur.fetchall()
                        for r in rows:
                            print(f"  {r[0]} ({r[1]})")
                    except Exception as e:
                        print(f"Error checking {t}: {e}")
                        conn.rollback()
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    inspect_schema()
