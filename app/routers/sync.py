from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import json

from app.db import get_db
from app.deps import require_auth
from app.models.core import SyncEvent, SyncCheckpoint  # assumes these exist

router = APIRouter(prefix="/sync", tags=["sync"])

def _ziso(dt: datetime) -> str:
    # RFC3339-like "Z" suffix
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

@router.post("/push")
def push(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),  # user id from your auth
    idemp_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    ops = body.get("ops", [])
    device_id = body.get("device_id")

    if not isinstance(ops, list):
        raise HTTPException(status_code=400, detail="ops must be a list")
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    # If client sent an idempotency key, short-circuit on duplicates
    if idemp_key:
        existing = (
            db.query(SyncCheckpoint)
              .filter(SyncCheckpoint.device_id == device_id,
                      SyncCheckpoint.key == idemp_key)
              .first()
        )
        if existing:
            # Return the saved result; the push is a no-op
            return {"stored": existing.stored_count, "idempotent": True}

    now = datetime.now(timezone.utc)
    events = []
    has_actor_column = hasattr(SyncEvent, "actor_user_id")

    for op in ops:
        # Minimal validation
        try:
            entity = op["entity"]
            entity_id = op["entity_id"]
            operation = op["op"]
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"missing field: {e.args[0]}")

        payload = op.get("payload")
        # Always carry actor_user_id in JSON payload for downstream processors
        if isinstance(payload, dict):
            if "actor_user_id" not in payload:
                payload = {**payload, "actor_user_id": sub}
        else:
            payload = {"value": payload, "actor_user_id": sub}

        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        kwargs = {}
        if has_actor_column:
            # If your table has this column, set it too (avoids NOT NULL violations later)
            kwargs["actor_user_id"] = sub

        ev = SyncEvent(
            entity=entity,
            entity_id=entity_id,
            op=operation,
            payload=payload_json,
            device_id=device_id,
            created_at=now,
            updated_at=now,
            **kwargs,
        )
        events.append(ev)

    if events:
        # Faster than N individual adds
        db.bulk_save_objects(events)
        db.flush()  # assign seq values

    stored = len(events)

    # Record idempotency for safe retries
    if idemp_key:
        # Make (device_id, key) unique at the DB level if possible
        cp = SyncCheckpoint(
            device_id=device_id,
            key=idemp_key,
            stored_count=stored,
            created_at=now,
            updated_at=now,
        )
        # merge = upsert-like for ORM objects keyed by a unique constraint
        db.merge(cp)

    db.commit()
    return {"stored": stored, "idempotent": bool(idemp_key)}

@router.get("/pull")
def pull(
    since: int = 0,
    limit: int = 1000,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    q = (
        db.query(SyncEvent)
          .filter(SyncEvent.seq > since)
          .order_by(SyncEvent.seq.asc())
          .limit(limit)
    )
    events = [
        {
            "seq": e.seq,
            "entity": e.entity,
            "entity_id": e.entity_id,
            "op": e.op,
            "payload": e.payload,
            "device_id": e.device_id,
            "updated_at": _ziso(e.updated_at),
        }
        for e in q
    ]
    next_since = events[-1]["seq"] if events else since
    return {"events": events, "next_since": next_since}
