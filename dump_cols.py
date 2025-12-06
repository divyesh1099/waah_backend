
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
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'menu_item';")
            rows = [r[0] for r in cur.fetchall()]
            rows.sort()
            print("COLUMNS_START")
            for r in rows:
                print(r)
            print("COLUMNS_END")
except Exception as e:
    print(e)
