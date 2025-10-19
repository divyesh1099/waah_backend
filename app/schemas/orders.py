from pydantic import BaseModel
from typing import Optional, Literal

OrderChannelLiteral = Literal["DINE_IN", "TAKEAWAY", "DELIVERY", "ONLINE"]
PayModeLiteral = Literal["CASH","CARD","UPI","WALLET","COUPON"]
OnlineProviderLiteral = Literal["ZOMATO","SWIGGY","CUSTOM"]

class OrderIn(BaseModel):
    tenant_id: str
    branch_id: str
    order_no: int
    channel: OrderChannelLiteral
    provider: Optional[OnlineProviderLiteral] = None
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
    line_discount: float = 0.0

class OrderItemOut(OrderItemIn):
    id: str

class PaymentIn(BaseModel):
    order_id: str
    mode: PayModeLiteral
    amount: float
    ref_no: Optional[str] = None

class PaymentOut(PaymentIn):
    id: str

class InvoiceOut(BaseModel):
    invoice_id: str
    invoice_no: str