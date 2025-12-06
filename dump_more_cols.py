
import sys
import os
import psycopg
sys.path.append(os.getcwd())
try:
    from app.db import engine
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"

    tables = ["item_variant", "menu_category", "kitchen_station"]

    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            for t in tables:
                print(f"--- {t} ---")
                cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{t}';")
                rows = [r[0] for r in cur.fetchall()]
                rows.sort()
                for r in rows:
                    print(r)
                print("")

except Exception as e:
    print(e)
