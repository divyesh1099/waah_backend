from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.db import get_db
from app.deps import require_perm
from app.models.core import BackupConfig, BackupRun

router = APIRouter(prefix="/backup", tags=["backup"]) 


@router.post("/config")
def upsert_config(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    # expects: tenant_id, branch_id, provider, local_dir?, endpoint?, bucket?, access_key?, secret_key?, schedule_cron?
    row = (
        db.query(BackupConfig)
        .filter(BackupConfig.tenant_id == body["tenant_id"], BackupConfig.branch_id == body["branch_id"]).first()
    )
    if not row:
        row = BackupConfig(**body)
        db.add(row)
    else:
        for k, v in body.items():
            setattr(row, k, v)
    db.commit(); db.refresh(row)
    return {"id": row.id}


@router.post("/run")
def record_run(config_id: str, ok: bool, bytes_total: int | None = None, location: str | None = None, error: str | None = None, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    r = BackupRun(
        config_id=config_id,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        ok=ok,
        bytes_total=bytes_total,
        location=location,
        error=error,
    )
    db.add(r); db.commit(); db.refresh(r)
    return {"run_id": r.id}


@router.get("/runs")
def list_runs(config_id: str | None = None, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    q = db.query(BackupRun)
    if config_id:
        q = q.filter(BackupRun.config_id == config_id)
    rows = q.order_by(BackupRun.created_at.desc()).limit(200).all()
    return [
        {
            "id": r.id,
            "config_id": r.config_id,
            "ok": r.ok,
            "bytes_total": r.bytes_total,
            "location": r.location,
            "error": r.error,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in rows
    ]
