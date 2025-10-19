from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session
from app.db import get_db
from app.deps import require_auth
from app.models.core import SyncEvent, SyncCheckpoint
from datetime import datetime, timezone
import json

router = APIRouter(prefix="/sync", tags=["sync"])

@router.post("/push")
def push(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_auth), idemp_key: str | None = Header(default=None, alias="Idempotency-Key")):
    # very simple: just store events, device updates its own checkpoint after pull
    ops = body.get("ops", [])
    device_id = body.get("device_id")
    now = datetime.now(timezone.utc)
    for op in ops:
        ev = SyncEvent(entity=op["entity"], entity_id=op["entity_id"], op=op["op"], payload=json.dumps(op.get("payload")), device_id=device_id, created_at=now, updated_at=now)
        db.add(ev)
    db.commit()
    return {"stored": len(ops)}

@router.get("/pull")
def pull(since: int = 0, limit: int = 1000, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    q = db.query(SyncEvent).filter(SyncEvent.seq > since).order_by(SyncEvent.seq.asc()).limit(limit)
    events = [{"seq": e.seq, "entity": e.entity, "entity_id": e.entity_id, "op": e.op, "payload": e.payload, "device_id": e.device_id, "updated_at": e.updated_at.isoformat()} for e in q]
    next_since = events[-1]["seq"] if events else since
    return {"events": events, "next_since": next_since}

