# app/db.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

# Keep your Base so models can `from app.db import Base`
class Base(DeclarativeBase):
    pass

# URL: prefer settings.DB_URL (your current setup), fallback to env
DATABASE_URL = getattr(settings, "DB_URL", None) or os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DB URL missing: set settings.DB_URL or env DATABASE_URL")

# Pool knobs (overridable via env)
POOL_SIZE    = int(os.getenv("DB_POOL_SIZE", "20"))      # was 5 → raise
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))   # was 10 → OK to raise
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))   # seconds to wait for a free conn
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800")) # recycle idle conns (30m)

# Optional echo (kept off unless you set it)
ECHO = bool(int(os.getenv("DB_ECHO", "0")))

connect_args = {}
# If you ever run SQLite in dev:
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT,
    pool_recycle=POOL_RECYCLE,
    future=True,
    echo=ECHO,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)

# ✅ CRITICAL: ensure rollback on error and always return the connection to the pool
def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
