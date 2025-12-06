
import sys
import os
sys.path.append(os.getcwd())
from app.db import engine
import psycopg

def fix_username():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    print("Connecting...")
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            # 1. Add username
            print("Adding username...")
            try:
                cur.execute('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS username VARCHAR(160)')
                print("Added username column")
            except Exception as e:
                print(f"Failed to add username: {e}")
                conn.rollback()
            
            # 2. Add unique constraint
            print("Adding constraint...")
            try:
                cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_user_username ON "user" (username)')
                print("Added unique index")
            except Exception as e:
                 print(f"Failed to add index (might exist): {e}")
                 # ignore if fails, merely safety
            
            conn.commit()
            print("Done.")

if __name__ == "__main__":
    fix_username()
