
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def check_specific():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'user';")
                cols = [row[0] for row in cur.fetchall()]
                print("User columns:", cols)
                
                check = ["branch_id", "pin_hash", "username"]
                for c in check:
                    print(f"{c} exists: {c in cols}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_specific()
