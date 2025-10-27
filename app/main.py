# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.middleware import RequestIdMiddleware
from app.db import Base, engine
from app.config import settings

# Routers
from app.routers import (
    identity, onboard, auth, dining, menu, orders, sync, kot, admin,
    users, customers, backup, reports, media, settings as settings_router,
    inventory, shift, printjob, online,
)

app = FastAPI(title="Waah API", version="0.3.0")

@app.on_event("startup")
def init_db():
    Base.metadata.create_all(bind=engine)

# Serve uploaded media
app.mount(
    settings.MEDIA_URL_BASE,
    StaticFiles(directory=settings.MEDIA_ROOT),
    name="media",
)

# Middlewares
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(onboard.router)
app.include_router(auth.router)
app.include_router(identity.router)
app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(sync.router)
app.include_router(kot.router)
app.include_router(admin.router)
app.include_router(settings_router.router)
app.include_router(backup.router)
app.include_router(reports.router)
app.include_router(media.router)
app.include_router(inventory.router)
app.include_router(shift.router)
app.include_router(printjob.router)
app.include_router(online.router)
app.include_router(users.router)
app.include_router(dining.router)
app.include_router(customers.router)

@app.get("/healthz")
def healthz():
    return {"ok": True}
