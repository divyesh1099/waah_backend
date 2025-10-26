from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_auth, require_perm
from app.models.core import Branch, RestaurantSettings, ChargeMode

router = APIRouter(prefix="/identity", tags=["identity"])

@router.get("/branches")
def list_branches(
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
):
    """
    List branches for a tenant. Used for:
    - branch picker
    - showing current branch info
    """
    q = db.query(Branch)
    if tenant_id:
        q = q.filter(Branch.tenant_id == tenant_id)

    branches = q.all()
    out = []
    for b in branches:
        out.append({
            "id": b.id,
            "tenant_id": b.tenant_id,
            "name": b.name,
            "phone": b.phone,
            "gstin": b.gstin,
            "state_code": b.state_code,
            "address": b.address,
        })
    return out


@router.post("/branches")
def create_branch(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Create a new branch under this tenant.
    Also seed a bare RestaurantSettings row so UI can immediately edit.
    """
    tenant_id = body.get("tenant_id")
    name = (body.get("name") or "").strip()

    if not tenant_id or not name:
        raise HTTPException(400, detail="tenant_id and name are required")

    b = Branch(
        tenant_id=tenant_id,
        name=name,
        phone=body.get("phone"),
        gstin=body.get("gstin"),
        state_code=body.get("state_code"),
        address=body.get("address"),
    )
    db.add(b)
    db.flush()  # we want b.id

    # seed RestaurantSettings so the edit sheet won't 400
    rs = RestaurantSettings(
        tenant_id=tenant_id,
        branch_id=b.id,
        name=name,
        address=b.address,
        phone=b.phone,
        gstin=b.gstin,
        fssai=None,
        print_fssai_on_invoice=False,
        gst_inclusive_default=True,
        service_charge_mode=ChargeMode.NONE,
        service_charge_value=0,
        packing_charge_mode=ChargeMode.NONE,
        packing_charge_value=0,
        invoice_footer="Thank you!",
    )
    db.add(rs)

    db.commit()
    db.refresh(b)

    return {"id": b.id}
