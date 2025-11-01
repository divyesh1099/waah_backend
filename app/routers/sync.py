# app/routers/sync.py
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import ProgrammingError, OperationalError
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum as EnumType
import json
from typing import Any

from app.db import get_db
from app.deps import require_auth
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
        # prefer float so consumers can math on it
        return float(o)
    if isinstance(o, EnumType):
        return o.value
    return str(o)

def _dumps(obj: Any) -> str:
    # Compact, unicode-safe, robust JSON
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_json_default)


@router.post("/push")
def push(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
    idemp_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    ops = body.get("ops", [])
    device_id = body.get("device_id")

    if not isinstance(ops, list):
        raise HTTPException(status_code=400, detail="ops must be a list")
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

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

    for op in ops:
        try:
            entity = op["entity"]
            entity_id = op["entity_id"]
            operation = op["op"]
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"missing field: {e.args[0]}")

        payload = op.get("payload")
        if isinstance(payload, dict):
            if "actor_user_id" not in payload:
                payload = {**payload, "actor_user_id": sub}
        else:
            payload = {"value": payload, "actor_user_id": sub}

        op["payload"] = payload  # <-- keep dict for applier

        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        extra = {"actor_user_id": sub} if has_actor_column else {}

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
    applied = 0
    try:
        applied = apply_ops(ops, db=db, user_id=sub, device_id=device_id)
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
    sub: str = Depends(require_auth),
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
        "payload": e.payload,  # already JSON text
        "device_id": e.device_id,
        "updated_at": _ziso(e.updated_at),
    } for e in q]

    next_since = events[-1]["seq"] if events else since
    return {"events": events, "next_since": next_since}
