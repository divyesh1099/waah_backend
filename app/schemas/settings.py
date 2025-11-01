from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional

class RestaurantSettingsIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    tenant_id: str
    branch_id: str

    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    gstin: Optional[str] = None
    invoice_footer: Optional[str] = None

    # IDs must be UUID-ish (≤36). We’ll accept objects/URLs in the router and resolve them.
    billing_printer_id: Optional[str] = Field(None, max_length=36)
    kitchen_printer_id: Optional[str] = Field(None, max_length=36)
    logo_media_id: Optional[str] = Field(None, max_length=36)

    @field_validator("billing_printer_id", "kitchen_printer_id", "logo_media_id")
    @classmethod
    def _id_len_guard(cls, v):
        if v is None:
            return v
        v = str(v).strip()
        if len(v) > 36:
            # Router will also accept flexible forms, but if the field is *explicitly* the *_id,
            # keep it strict so we return 422/400 instead of 500 from the DB layer.
            raise ValueError("Expect an ID (UUID length ≤ 36)")
        return v or None
