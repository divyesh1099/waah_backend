
import sys
import os
sys.path.append(os.getcwd())
from app.db import engine
import psycopg

def fix_branch():
    url = engine.url
    conn_str = f"dbname={url.database} user={url.username} password={url.password} host={url.host} port={url.port}"
    
    print("Connecting...")
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            # Add code
            print("Adding code to branch...")
            try:
                cur.execute('ALTER TABLE "branch" ADD COLUMN IF NOT EXISTS code VARCHAR(50)')
                # default to something if rows exist? or nullable? 
                # Model says Mapped[str] -> NOT NULL.
                # If rows exist, we might need a default.
                # Let's try adding nullable first or with default?
                # But typically for these fixes, we just add it. Sla usually handles defaults if logic provided, but here raw SQL.
                # Code might be required. I will update it to be nullable for now IF logic permits, but model says required.
                # I'll update it to have a default value for existing rows.
                cur.execute("UPDATE branch SET code = 'MAIN' WHERE code IS NULL")
                print("Added code column")
            except Exception as e:
                print(f"Failed to add code: {e}")
                conn.rollback()
            
            conn.commit()
            print("Done.")

if __name__ == "__main__":
    fix_branch()
