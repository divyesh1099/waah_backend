
import logging
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger("uvicorn")

def check_and_fix_schema(engine: Engine):
    """
    Checks for missing columns/indexes that might have been introduced recently
    and adds them using raw SQL if they are missing.
    This works around 'create_all' not updating existing tables.
    """
    logger.info("Checking database schema for missing columns...")
    
    with engine.connect() as conn:
        # 1. user.branch_id
        try:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS branch_id VARCHAR(36)'))
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to check/add user.branch_id: {e}")
            conn.rollback()

        # 2. user.username
        try:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS username VARCHAR(160)'))
            # Add unique index if username exists
            try:
                conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_user_username ON "user" (username)'))
            except Exception:
                pass 
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to check/add user.username: {e}")
            conn.rollback()
            
             
        # 3. branch.code
        try:
            # We need to know if we are adding it, because if existing rows have NULL, it might be an issue.
            # But 'ADD COLUMN' with default is safe.
            # However, simpler to just add it nullable first, then update defaults?
            # 'IF NOT EXISTS' is standard in Postgres 9.6+
            conn.execute(text('ALTER TABLE "branch" ADD COLUMN IF NOT EXISTS code VARCHAR(50)'))
            
            # Backfill any nulls just in case (e.g. 'MAIN')
            conn.execute(text("UPDATE branch SET code = 'MAIN' WHERE code IS NULL"))
            
            conn.commit()
        except Exception as e:
             logger.error(f"Failed to check/add branch.code: {e}")
             conn.rollback()

        # 4. order.order_no type fix (Integer -> Varchar)
        try:
            # Check current type
            # Note: "order" is a reserved word so we query carefully or rely on string 'order'
            result = conn.execute(text("SELECT data_type FROM information_schema.columns WHERE table_name = 'order' AND column_name = 'order_no'"))
            row = result.fetchone()
            if row and row[0] != 'character varying':
                logger.info(f"Fixing order.order_no type (current: {row[0]})")
                conn.execute(text('ALTER TABLE "order" ALTER COLUMN order_no TYPE VARCHAR(60) USING order_no::VARCHAR'))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to fix order.order_no type: {e}")
            conn.rollback()
             
    logger.info("Schema check complete.")
