from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Literal

OrderChannelLiteral = Literal["DINE_IN", "TAKEAWAY", "DELIVERY", "ONLINE"]
PayModeLiteral = Literal["CASH","CARD","UPI","WALLET","COUPON"]
OnlineProviderLiteral = Literal["ZOMATO","SWIGGY","CUSTOM"]
OrderStatusLiteral = Literal["OPEN", "KITCHEN", "READY", "SERVED", "CLOSED", "VOID"]

class OrderIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    tenant_id: str
    branch_id: str
    order_no: str
    channel: OrderChannelLiteral
    provider: Optional[OnlineProviderLiteral] = None
    table_id: Optional[str] = None
    customer_id: Optional[str] = None
    pax: Optional[int] = Field(None, ge=1)
    note: Optional[str] = None

class OrderOut(OrderIn):
    id: str
    status: str

class OrderItemIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    order_id: str
    item_id: str
    variant_id: Optional[str] = None
    parent_line_id: Optional[str] = None
    qty: float = Field(..., gt=0)
    unit_price: float = Field(..., ge=0)
    line_discount: float = Field(0.0, ge=0)

class OrderItemOut(OrderItemIn):
    id: str

class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    order_id: str
    mode: PayModeLiteral
    amount: float = Field(..., gt=0)
    ref_no: Optional[str] = None

class PaymentOut(PaymentIn):
    id: str

class InvoiceOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    invoice_id: str
    invoice_no: str

class OrderStatusUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    status: OrderStatusLiteral
    reason: Optional[str] = None # For auditing, e.g., if moving to VOID