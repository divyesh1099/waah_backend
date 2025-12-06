
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def remove_table_fk():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"

    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                print("Removing foreign key constraint on order.table_id...")
                try:
                    cur.execute('ALTER TABLE "order" DROP CONSTRAINT IF EXISTS order_table_id_fkey;')
                    conn.commit()
                    print("SUCCESS: Foreign key constraint removed.")
                except Exception as e:
                    print(f"FAILED: {e}")
                    conn.rollback()
                    
    except Exception as e:
        print(f"Connection error: {e}")

if __name__ == "__main__":
    remove_table_fk()
