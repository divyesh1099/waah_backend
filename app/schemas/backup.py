from pydantic import BaseModel
from typing import Optional, Literal

BackupProviderLiteral = Literal["NONE","S3","GDRIVE","AZURE"]

class BackupConfigIn(BaseModel):
    tenant_id: str
    branch_id: str
    provider: BackupProviderLiteral = "NONE"
    local_dir: Optional[str] = None
    endpoint: Optional[str] = None
    bucket: Optional[str] = None
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    schedule_cron: Optional[str] = None

class BackupConfigOut(BackupConfigIn):
    id: str

class BackupRunOut(BaseModel):
    id: str
    config_id: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    ok: bool
    bytes_total: Optional[int] = None
    location: Optional[str] = None
    error: Optional[str] = None