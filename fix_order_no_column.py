
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def fix():
    url = engine.url
    # sslmode=disable is important for local/docker usually, depending on setup
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port} sslmode=disable"
    
    print(f"Connecting to {url.host}...")
    try:
        with psycopg.connect(conn_str) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                print("Checking order_no type...")
                cur.execute("""
                    SELECT data_type 
                    FROM information_schema.columns 
                    WHERE table_name = 'order' AND column_name = 'order_no';
                """)
                row = cur.fetchone()
                if row:
                    dtype = row[0]
                    print(f"Current type: {dtype}")
                    if dtype != 'character varying':
                        print("Altering order_no to VARCHAR(60)...")
                        # We need to cast existing integers to string if any
                        cur.execute('ALTER TABLE "order" ALTER COLUMN order_no TYPE VARCHAR(60) USING order_no::VARCHAR;')
                        print("Done.")
                    else:
                        print("Already VARCHAR.")
                else:
                    print("Column order_no not found!")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fix()
