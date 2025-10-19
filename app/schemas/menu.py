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
    description: Optional[str] = None
    sku: Optional[str] = None
    hsn: Optional[str] = None
    is_active: bool = True
    stock_out: bool = False
    tax_inclusive: bool = True
    gst_rate: float = 5.0
    kitchen_station_id: Optional[str] = None

class MenuItemOut(MenuItemIn):
    id: str

class VariantIn(BaseModel):
    item_id: str
    label: str
    base_price: float
    mrp: Optional[float] = None
    is_default: bool = False

class VariantOut(VariantIn):
    id: str

class ModifierGroupIn(BaseModel):
    tenant_id: str
    name: str
    min_sel: int = 0
    max_sel: Optional[int] = None
    required: bool = False

class ModifierGroupOut(ModifierGroupIn):
    id: str

class ModifierIn(BaseModel):
    group_id: str
    name: str
    price_delta: float = 0.0

class ModifierOut(ModifierIn):
    id: str

class ItemModifierGroupIn(BaseModel):
    item_id: str
    group_id: str

class ItemModifierGroupOut(ItemModifierGroupIn):
    # Composite key table; echo back the same payload
    pass

