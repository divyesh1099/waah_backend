
import sys
import os
import psycopg
sys.path.append(os.getcwd())
from app.db import engine

def audit():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    expected = [
        "id", "tenant_id", "branch_id", "order_no", "channel", 
        "provider", "status", "table_id", "customer_id", 
        "opened_by_user_id", "closed_by_user_id", "pax", 
        "source_device_id", "note", "opened_at", "closed_at"
    ]

    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                print("Checking 'order' table...")
                cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = 'order';")
                actual = {row[0] for row in cur.fetchall()}
                
                # 'order' is a reserved word, sqlalchemy uses "order" but table_name in info schema might be just 'order'
                # Let's check if it returns empty, it might be named something else?
                # Models: __tablename__ = "order"
                
                print(f"Actual columns (count {len(actual)}): {actual}")
                for c in expected:
                    if c not in actual:
                        print(f"MISSING in order: {c}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    audit()
