from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List

class MenuCategoryIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    tenant_id: str
    branch_id: str
    name: str = Field(min_length=1)
    position: int = Field(0, ge=0)

class MenuCategoryOut(MenuCategoryIn):
    id: str

class MenuItemIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    tenant_id: str
    category_id: str
    name: str = Field(min_length=1)
    description: Optional[str] = None
    sku: Optional[str] = None
    hsn: Optional[str] = None
    is_active: bool = True
    stock_out: bool = False
    tax_inclusive: bool = True
    gst_rate: float = Field(5.0, ge=0, le=28)
    kitchen_station_id: Optional[str] = None

class MenuItemOut(MenuItemIn):
    id: str

class VariantIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    item_id: str
    label: str = Field(min_length=1)
    base_price: float = Field(..., ge=0)
    mrp: Optional[float] = Field(None, ge=0)
    is_default: bool = False

class VariantOut(VariantIn):
    id: str

class ModifierGroupIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    tenant_id: str
    name: str = Field(min_length=1)
    min_sel: int = Field(0, ge=0)
    max_sel: Optional[int] = Field(None, ge=0)
    required: bool = False

class ModifierGroupOut(ModifierGroupIn):
    id: str

class ModifierIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    group_id: str
    name: str = Field(min_length=1)
    price_delta: float = Field(0.0, ge=0)

class ModifierOut(ModifierIn):
    id: str

class ItemModifierGroupIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    item_id: str
    group_id: str


class ItemModifierGroupOut(ItemModifierGroupIn):
    pass


# ---------- Bulk Insert Schemas ----------

class VariantBulkIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # item_id is inferred from parent
    label: str = Field(min_length=1)
    base_price: float = Field(..., ge=0)
    mrp: Optional[float] = Field(None, ge=0)
    is_default: bool = False

class ItemBulkIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    # category_id is inferred from parent
    name: str = Field(min_length=1)
    description: Optional[str] = None
    sku: Optional[str] = None
    hsn: Optional[str] = None
    is_active: bool = True
    stock_out: bool = False
    tax_inclusive: bool = True
    gst_rate: float = Field(5.0, ge=0, le=28)
    kitchen_station_id: Optional[str] = None
    # Nested variants
    variants: Optional[List[VariantBulkIn]] = None

class CategoryBulkIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    # tenant_id, branch_id from context
    name: str = Field(min_length=1)
    position: int = Field(0, ge=0)
    # Nested items
    items: Optional[List[ItemBulkIn]] = None

class BulkMenuIn(BaseModel):
    categories: List[CategoryBulkIn]
