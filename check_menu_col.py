
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def inspect_menu_item():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                tables = ["item_variant", "menu_category"]
                for t in tables:
                    print(f"--- Table: {t} ---")
                    try:
                        cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{t}';")
                        rows = cur.fetchall()
                        if not rows:
                            print(f"  TABLE {t} NOT FOUND!")
                            continue
                        for r in rows:
                            print(f"  {r[0]} ({r[1]})")
                    except Exception as e:
                        print(f"Error reading {t}: {e}")
                            
    except Exception as e:
        print(f"Connection failed: {e}")
                    
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    inspect_menu_item()
