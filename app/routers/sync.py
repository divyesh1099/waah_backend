# app/routes/sync.py
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import json

from app.db import get_db
from app.deps import require_auth
from app.models.core import SyncEvent, SyncIdempotency  # <- use dedicated idempotency table

router = APIRouter(prefix="/sync", tags=["sync"])

def _ziso(dt: datetime | None) -> str | None:
    """Return RFC3339-like string with trailing 'Z', or None if dt is None."""
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

@router.post("/push")
def push(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),  # authenticated user id
    idemp_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """
    Accepts a batch of ops and stores them as SyncEvent rows.
    If Idempotency-Key is provided, the push is made idempotent per (device_id, key).
    """
    ops = body.get("ops", [])
    device_id = body.get("device_id")

    if not isinstance(ops, list):
        raise HTTPException(status_code=400, detail="ops must be a list")
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    # Short-circuit on duplicate idempotency keys for the same device
    if idemp_key:
        existing = (
            db.query(SyncIdempotency)
              .filter(
                  SyncIdempotency.device_id == device_id,
                  SyncIdempotency.key == idemp_key,
              )
              .first()
        )
        if existing:
            return {"stored": existing.stored_count, "idempotent": True}

    now = datetime.now(timezone.utc)
    events: list[SyncEvent] = []
    has_actor_column = hasattr(SyncEvent, "actor_user_id")  # safe if you didn't add the column

    for op in ops:
        try:
            entity = op["entity"]
            entity_id = op["entity_id"]
            operation = op["op"]  # e.g. UPSERT/DELETE
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"missing field: {e.args[0]}")

        payload = op.get("payload")

        # Ensure actor is present for downstream processors / audit
        if isinstance(payload, dict):
            if "actor_user_id" not in payload:
                payload = {**payload, "actor_user_id": sub}
        else:
            payload = {"value": payload, "actor_user_id": sub}

        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        extra = {}
        if has_actor_column:
            # If you added SyncEvent.actor_user_id, fill it as well.
            extra["actor_user_id"] = sub

        events.append(
            SyncEvent(
                entity=entity,
                entity_id=entity_id,
                op=operation,
                payload=payload_json,
                device_id=device_id,
                created_at=now,
                updated_at=now,
                **extra,
            )
        )

    if events:
        # Faster than add() in a loop; flush to populate autoincrement seq
        db.bulk_save_objects(events)
        db.flush()

    stored = len(events)

    # Record idempotency outcome so retries are safe
    if idemp_key:
        db.merge(
            SyncIdempotency(
                device_id=device_id,
                key=idemp_key,
                stored_count=stored,
                created_at=now,
                updated_at=now,
            )
        )

    db.commit()
    return {"stored": stored, "idempotent": bool(idemp_key)}

@router.get("/pull")
def pull(
    since: int = 0,
    limit: int = 1000,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    Return events with seq > since, ascending by seq, up to `limit`.
    Client should pass back next_since for the next page.
    """
    # small guardrails
    if since < 0:
        since = 0
    if limit <= 0 or limit > 5000:
        limit = 1000

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
