# (Ensure package markers)
# app/__init__.py, app/models/__init__.py, app/routers/__init__.py, app/schemas/__init__.py, app/util/__init__.py
# all can be empty files to make packages.

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.middleware import RequestIdMiddleware
from app.routers import auth, menu, orders, sync, kot, admin, settings as settings_router
from app.db import Base, engine
from app.config import settings

app = FastAPI(title="Waah API", version="0.1.0")
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(sync.router)
app.include_router(kot.router)
app.include_router(admin.router)
app.include_router(settings_router.router)

@app.on_event("startup")
def _startup_create_tables():
    # Import models at startup to register them, then create tables (dev only)
    if settings.APP_ENV == "dev":
        import app.models  # noqa: F401
        Base.metadata.create_all(bind=engine)


@app.get("/healthz")
def healthz():
    return {"ok": True}
