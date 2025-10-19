from pydantic import BaseModel
from typing import Optional

class ReportDailySalesOut(BaseModel):
    id: str
    date: str
    tenant_id: str
    branch_id: str
    channel: Optional[str] = None
    provider: Optional[str] = None
    orders_count: int
    gross: float
    tax: float
    cgst: float
    sgst: float
    igst: float
    discounts: float
    net: float

class ReportStockSnapshotOut(BaseModel):
    id: str
    at_date: str
    ingredient_id: str
    opening_qty: float
    purchased_qty: float
    used_qty: float
    closing_qty: float
