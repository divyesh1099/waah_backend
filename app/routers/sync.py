# ✅ DROP‑IN PATCH — Sync & Shift fixes (AuthCtx serialization + permission checks)
# Place these as full replacements for your files.
# Files:
#   1) app/routers/sync.py
#   2) app/routers/shift.py

# -----------------------------------------------------------------------------
# 1) app/routers/sync.py  (FULL FILE)
# -----------------------------------------------------------------------------
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import ProgrammingError, OperationalError
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum as EnumType
from typing import Any
import json

from app.db import get_db
from app.deps import require_auth  # returns AuthCtx (not a str)
from app.models.core import SyncEvent, SyncIdempotency
from app.routers.sync_apply import apply_ops

router = APIRouter(prefix="/sync", tags=["sync"])


def _ziso(dt: datetime | None) -> str | None:
    return None if not dt else dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_default(o: Any):
    # Accept datetimes, decimals, enums; fall back to str for odd types
    if isinstance(o, datetime):
        return o.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, EnumType):
        return o.value
    return str(o)


def _dumps(obj: Any) -> str:
    # Compact, unicode-safe, robust JSON
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _uid(x: Any) -> str:
    """Extract a user id from AuthCtx/str-like values."""
    if x is None:
        return ""
    for attr in ("user_id", "sub", "id"):
        if hasattr(x, attr):
            val = getattr(x, attr)
            return str(val)
    return str(x)


@router.post("/push")
def push(
    body: dict,
    db: Session = Depends(get_db),
    sub: Any = Depends(require_auth),  # may be AuthCtx; never assume str
    idemp_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Accepts ops and records SyncEvent; applies them in same transaction.

    Body example:
      {"device_id": "POS1", "ops": [{"entity": "Order","entity_id": "X1","op":"UPSERT","payload": {...}}]}
    """
    ops = body.get("ops", [])
    device_id = body.get("device_id")

    if not isinstance(ops, list):
        raise HTTPException(status_code=400, detail="ops must be a list")
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    actor_id = _uid(sub)

    # --- idempotency probe (if table present) -------------------------------
    idem_supported = True
    if idemp_key:
        try:
            existing = (
                db.query(SyncIdempotency)
                  .filter(SyncIdempotency.device_id == device_id,
                          SyncIdempotency.key == idemp_key)
                  .first()
            )
            if existing:
                return {"stored": existing.stored_count, "applied": 0, "idempotent": True}
        except (ProgrammingError, OperationalError):
            db.rollback()
            idem_supported = False

    now = datetime.now(timezone.utc)
    events = []
    has_actor_column = hasattr(SyncEvent, "actor_user_id")

    # Normalize ops & build events (do NOT put AuthCtx in payload)
    for op in ops:
        try:
            entity = op["entity"]
            entity_id = op["entity_id"]
            operation = op["op"]
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"missing field: {e.args[0]}")

        raw_payload = op.get("payload")
        if isinstance(raw_payload, dict):
            payload = {**raw_payload, "actor_user_id": actor_id}
        else:
            payload = {"value": raw_payload, "actor_user_id": actor_id}

        # Keep dict form for applier
        op["payload"] = payload

        payload_json = _dumps(payload)
        extra = {"actor_user_id": actor_id} if has_actor_column else {}

        events.append(SyncEvent(
            entity=entity,
            entity_id=entity_id,
            op=operation,
            payload=payload_json,
            device_id=device_id,
            created_at=now,
            updated_at=now,
            **extra,
        ))

    # Apply to domain tables BEFORE committing (same txn)
    try:
        applied = apply_ops(ops, db=db, user_id=actor_id, device_id=device_id)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"sync apply failed: {e}")

    if events:
        db.bulk_save_objects(events)
        db.flush()

    stored = len(events)

    if idemp_key and idem_supported:
        try:
            db.merge(SyncIdempotency(
                device_id=device_id,
                key=idemp_key,
                stored_count=stored,
                created_at=now,
                updated_at=now,
            ))
        except (ProgrammingError, OperationalError):
            db.rollback()
            return {"stored": stored, "applied": applied, "idempotent": False}

    db.commit()
    return {"stored": stored, "applied": applied, "idempotent": bool(idemp_key and idem_supported)}


@router.get("/pull")
def pull(
    since: int = 0,
    limit: int = 1000,
    db: Session = Depends(get_db),
    sub: Any = Depends(require_auth),
):
    if since < 0:
        since = 0
    if limit <= 0 or limit > 5000:
        limit = 1000

    q = (db.query(SyncEvent)
           .filter(SyncEvent.seq > since)
           .order_by(SyncEvent.seq.asc())
           .limit(limit))

    events = [{
        "seq": e.seq,
        "entity": e.entity,
        "entity_id": e.entity_id,
        "op": e.op,
        "payload": e.payload,  # JSON text already
        "device_id": e.device_id,
        "updated_at": _ziso(e.updated_at),
    } for e in q]

    next_since = events[-1]["seq"] if events else since
    return {"events": events, "next_since": next_since}
