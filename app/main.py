# app/main.py
# (Ensure package markers)
# app/__init__.py, app/models/__init__.py, app/routers/__init__.py,
# app/schemas/__init__.py, app/util/__init__.py — can be empty files.

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.middleware import RequestIdMiddleware
from app.db import Base, engine
from app.config import settings

# Routers (keep existing)
from app.routers import onboard, auth, dining, menu, orders, sync, kot, admin, users, customers
from app.routers import settings as settings_router
from app.routers import backup, reports

# New routers wired for the new models / features
from app.routers import inventory, shift, printjob, online

app = FastAPI(title="Waah API", version="0.3.0")

@app.on_event("startup")
def init_db():
    Base.metadata.create_all(bind=engine)

# Middlewares
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Keep existing includes
app.include_router(onboard.router)
app.include_router(auth.router)
app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(sync.router)
app.include_router(kot.router)
app.include_router(admin.router)
app.include_router(settings_router.router)
app.include_router(backup.router)
app.include_router(reports.router)

# Add the missing ones
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
