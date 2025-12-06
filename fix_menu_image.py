
import sys
import os
import psycopg
sys.path.append(os.getcwd())
try:
    from app.db import engine
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"

    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            print("Adding image_url to menu_item...")
            try:
                cur.execute("ALTER TABLE menu_item ADD COLUMN image_url VARCHAR(400);")
                print("SUCCESS: Added image_url.")
            except Exception as e:
                print(f"FAILED (maybe exists?): {e}")
                
except Exception as e:
    print(f"Connection error: {e}")
