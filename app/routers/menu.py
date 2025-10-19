from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.db import get_db
from app.schemas.menu import MenuCategoryIn, MenuCategoryOut, MenuItemIn, MenuItemOut, VariantIn, VariantOut
from app.models.core import MenuCategory, MenuItem, ItemVariant
from app.deps import require_auth, require_perm

router = APIRouter(prefix="/menu", tags=["menu"])

@router.post("/categories", response_model=MenuCategoryOut)
def create_category(body: MenuCategoryIn, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    cat = MenuCategory(**body.model_dump())
    db.add(cat); db.commit(); db.refresh(cat)
    return MenuCategoryOut(id=cat.id, **body.model_dump())

@router.get("/categories", response_model=List[MenuCategoryOut])
def list_categories(tenant_id: str, branch_id: str, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    rows = (db.query(MenuCategory)
              .filter(MenuCategory.tenant_id==tenant_id, MenuCategory.branch_id==branch_id,
                      MenuCategory.deleted_at.is_(None))
              .order_by(MenuCategory.position).all())
    return [MenuCategoryOut(id=r.id, tenant_id=r.tenant_id, branch_id=r.branch_id, name=r.name, position=r.position) for r in rows]

@router.post("/items", response_model=MenuItemOut)
def create_item(body: MenuItemIn, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    it = MenuItem(**body.model_dump())
    db.add(it); db.commit(); db.refresh(it)
    return MenuItemOut(id=it.id, **body.model_dump())

@router.patch("/items/{item_id}")
def update_item(item_id: str, body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    it = db.get(MenuItem, item_id)
    if not it:
        raise HTTPException(404, "item not found")
    for k in ["name","description","gst_rate","tax_inclusive","kitchen_station_id","is_active","hsn","sku"]:
        if k in body:
            setattr(it, k, body[k])
    db.commit()
    return {"id": it.id}

@router.post("/items/{item_id}/availability")
def set_item_availability(item_id: str, stock_out: bool, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    it = db.get(MenuItem, item_id)
    if not it:
        raise HTTPException(404, "item not found")
    it.stock_out = bool(stock_out)
    db.commit()
    return {"id": it.id, "stock_out": it.stock_out}

@router.post("/variants", response_model=VariantOut)
def create_variant(body: VariantIn, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    v = ItemVariant(**body.model_dump())
    db.add(v); db.commit(); db.refresh(v)
    return VariantOut(id=v.id, **body.model_dump())

@router.patch("/items/{item_id}")
def update_item(item_id: str, body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    it = db.get(MenuItem, item_id)
    if not it:
        raise HTTPException(404, "item not found")
    for k in ["name","description","gst_rate","tax_inclusive","kitchen_station_id","is_active","hsn","sku"]:
        if k in body:
            setattr(it, k, body[k])
    db.commit()
    return {"id": it.id}

@router.post("/items/{item_id}/availability")
def set_item_availability(item_id: str, stock_out: bool, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    it = db.get(MenuItem, item_id)
    if not it:
        raise HTTPException(404, "item not found")
    it.stock_out = bool(stock_out)
    db.commit()
    return {"id": it.id, "stock_out": it.stock_out}
