from pydantic import BaseModel
from typing import Optional, Literal

class OrderIn(BaseModel):
    tenant_id: str
    branch_id: str
    order_no: int
    channel: Literal["DINE_IN","TAKEAWAY","DELIVERY","ONLINE"]
    table_id: Optional[str] = None
    customer_id: Optional[str] = None
    pax: Optional[int] = None
    note: Optional[str] = None

class OrderOut(OrderIn):
    id: str
    status: str

class OrderItemIn(BaseModel):
    order_id: str
    item_id: str
    variant_id: Optional[str] = None
    qty: float
    unit_price: float

class PaymentIn(BaseModel):
    order_id: str
    mode: Literal["CASH","CARD","UPI","WALLET","COUPON"]
    amount: float
    ref_no: Optional[str] = None
