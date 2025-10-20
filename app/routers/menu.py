from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db import get_db
from app.schemas.menu import MenuCategoryIn, MenuCategoryOut, MenuItemIn, MenuItemOut, VariantIn, VariantOut
from app.models.core import MenuCategory, MenuItem, ItemVariant, ModifierGroup, Modifier, ItemModifierGroup
from app.deps import require_auth, require_perm

router = APIRouter(prefix="/menu", tags=["menu"]) 


@router.post("/categories", response_model=MenuCategoryOut)
def create_category(body: MenuCategoryIn, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    cat = MenuCategory(**body.model_dump())
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return MenuCategoryOut(id=cat.id, **body.model_dump())


@router.get("/categories", response_model=List[MenuCategoryOut])
def list_categories(tenant_id: str, branch_id: str, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    rows = (
        db.query(MenuCategory)
        .filter(
            MenuCategory.tenant_id == tenant_id,
            MenuCategory.branch_id == branch_id,
            MenuCategory.deleted_at.is_(None),
        )
        .order_by(MenuCategory.position)
        .all()
    )
    return [
        MenuCategoryOut(
            id=r.id, tenant_id=r.tenant_id, branch_id=r.branch_id, name=r.name, position=r.position
        )
        for r in rows
    ]


@router.post("/items", response_model=MenuItemOut)
def create_item(body: MenuItemIn, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    it = MenuItem(**body.model_dump())
    db.add(it)
    db.commit()
    db.refresh(it)
    return MenuItemOut(id=it.id, **body.model_dump())


@router.post("/variants", response_model=VariantOut)
def create_variant(body: VariantIn, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    v = ItemVariant(**body.model_dump())
    db.add(v)
    db.commit()
    db.refresh(v)
    return VariantOut(id=v.id, **body.model_dump())


@router.post("/items/{item_id}/stock_out")
def set_stock_out(item_id: str, value: bool, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    it = db.get(MenuItem, item_id)
    if not it:
        raise HTTPException(404, detail="item not found")
    it.stock_out = bool(value)
    db.commit()
    return {"id": it.id, "stock_out": it.stock_out}


@router.post("/items/{item_id}/assign_station")
def assign_station(item_id: str, station_id: str | None, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    it = db.get(MenuItem, item_id)
    if not it:
        raise HTTPException(404, detail="item not found")
    it.kitchen_station_id = station_id
    db.commit()
    return {"id": it.id, "kitchen_station_id": it.kitchen_station_id}


@router.post("/items/{item_id}/update_tax")
def update_tax(item_id: str, gst_rate: float, tax_inclusive: bool = True, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    it = db.get(MenuItem, item_id)
    if not it:
        raise HTTPException(404, detail="item not found")
    it.gst_rate = gst_rate
    it.tax_inclusive = tax_inclusive
    db.commit()
    return {"id": it.id, "gst_rate": float(it.gst_rate), "tax_inclusive": bool(it.tax_inclusive)}

@router.post("/modifier_groups")
def create_modifier_group(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    """
    body: {tenant_id: str, name: str, min_sel: int, max_sel: int}
    """
    mg = ModifierGroup(**body)
    db.add(mg); db.commit(); db.refresh(mg)
    return {"id": mg.id}

@router.post("/modifiers")
def create_modifier(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    """
    body: {group_id: str, name: str, price_delta: float}
    """
    # ensure group exists
    if not db.get(ModifierGroup, body.get("group_id")):
        raise HTTPException(404, detail="modifier group not found")
    m = Modifier(**body)
    db.add(m); db.commit(); db.refresh(m)
    return {"id": m.id}

@router.post("/items/{item_id}/modifier_groups")
def link_item_group(item_id: str, body: dict, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    """
    body: {group_id: str}
    """
    if not db.get(MenuItem, item_id):
        raise HTTPException(404, detail="menu item not found")
    group_id = body.get("group_id")
    if not db.get(ModifierGroup, group_id):
        raise HTTPException(404, detail="modifier group not found")

    exists = (
        db.query(ItemModifierGroup)
        .filter(ItemModifierGroup.item_id == item_id, ItemModifierGroup.group_id == group_id)
        .first()
    )
    if not exists:
        link = ItemModifierGroup(item_id=item_id, group_id=group_id)
        db.add(link); db.commit()
        return {"ok": True, "linked": True}
    return {"ok": True, "linked": False}
