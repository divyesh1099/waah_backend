import json
from sqlalchemy.orm import Session
from app.models.core import AuditLog

def audit(db: Session, actor_user_id: str, entity: str, entity_id: str,
          action: str, before: dict | None = None, after: dict | None = None, reason: str | None = None):
    entry = AuditLog(
        actor_user_id=actor_user_id,
        entity=entity, entity_id=entity_id,
        action=action if not reason else f"{action}:{reason}",
        before=json.dumps(before) if before else None,
        after=json.dumps(after) if after else None,
    )
    db.add(entry)
