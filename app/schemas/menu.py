from pydantic import BaseModel
from typing import Optional

class MenuCategoryIn(BaseModel):
    tenant_id: str
    branch_id: str
    name: str
    position: int = 0

class MenuCategoryOut(MenuCategoryIn):
    id: str

class MenuItemIn(BaseModel):
    tenant_id: str
    category_id: str
    name: str
    sku: Optional[str] = None
    hsn: Optional[str] = None
    tax_inclusive: bool = True

class MenuItemOut(MenuItemIn):
    id: str

class VariantIn(BaseModel):
    item_id: str
    label: str
    base_price: float
    is_default: bool = False

class VariantOut(VariantIn):
    id: str

