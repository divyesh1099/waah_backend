from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

from app.db import get_db
from app.schemas.menu import (
    MenuCategoryIn,
    MenuCategoryOut,
    MenuItemIn,
    MenuItemOut,
    VariantIn,
    VariantOut,
)
from app.models.core import (
    Branch,
    MenuCategory,
    MenuItem,
    ItemVariant,
    ModifierGroup,
    Modifier,
    ItemModifierGroup,
)
from app.deps import AuthCtx, require_auth, require_perm

router = APIRouter(prefix="/menu", tags=["menu"])


# ---------- helpers ----------

def _as_float(val: Decimal | float | int | None) -> float | None:
    if val is None:
        return None
    return float(val)

def _ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # send ISO8601 that Flutter DateTime.tryParse() can eat
    return dt.isoformat()

def _item_payload(m: MenuItem) -> dict:
    """Consistent shape for MenuItem back to Flutter."""
    return {
        "id": m.id,
        "tenant_id": m.tenant_id,
        # no branch_id field on model today

        "name": m.name,
        "description": m.description,
        "category_id": m.category_id,
        "sku": m.sku,
        "hsn": m.hsn,

        "is_active": bool(m.is_active),
        "stock_out": bool(m.stock_out),
        "tax_inclusive": bool(m.tax_inclusive),
        "gst_rate": _as_float(m.gst_rate) or 0.0,

        "kitchen_station_id": m.kitchen_station_id,

        "created_at": _ts(getattr(m, "created_at", None)),
        "updated_at": _ts(getattr(m, "updated_at", None)),
    }

def _variant_payload(v: ItemVariant) -> dict:
    return {
        "id": v.id,
        "item_id": v.item_id,
        "label": v.label,
        "mrp": _as_float(v.mrp),
        "base_price": _as_float(v.base_price) or 0.0,
        "is_default": bool(v.is_default),
    }

def _category_payload(c: MenuCategory) -> dict:
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "branch_id": c.branch_id,
        "name": c.name,
        "position": c.position,
    }


def _ensure_category_access(db: Session, cat_id: str, ctx: AuthCtx) -> MenuCategory:
    cat = db.get(MenuCategory, cat_id)
    if (not cat) or getattr(cat, "deleted_at", None) is not None:
        raise HTTPException(status_code=404, detail="category not found")
    if cat.tenant_id != ctx.tenant_id:
        # hide existence across tenants
        raise HTTPException(status_code=404, detail="category not found")
    if ctx.branch_id and cat.branch_id != ctx.branch_id:
        raise HTTPException(status_code=404, detail="category not found")
    return cat

def _ensure_item_access(db: Session, item_id: str, ctx: AuthCtx) -> MenuItem:
    it = db.get(MenuItem, item_id)
    if (not it) or getattr(it, "deleted_at", None) is not None:
        raise HTTPException(status_code=404, detail="item not found")
    if it.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="item not found")
    # enforce branch through its category
    cat = db.get(MenuCategory, it.category_id)
    if (not cat) or cat.tenant_id != ctx.tenant_id or (ctx.branch_id and cat.branch_id != ctx.branch_id):
        raise HTTPException(status_code=404, detail="item not found")
    return it

def _effective_branch_id(db: Session, ctx: AuthCtx, provided_branch_id: Optional[str]) -> str:
    """
    Decide which branch to use:
      - If token has ctx.branch_id => use it.
      - Else, require caller to provide branch_id and verify it belongs to ctx.tenant_id.
    """
    bid = (ctx.branch_id or (provided_branch_id or "").strip())
    if not bid:
        raise HTTPException(status_code=400, detail="branch not selected")
    br = db.get(Branch, bid)
    if (not br) or br.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="branch not found")
    return bid


# ---------- ITEMS (for POS grid etc) ----------

@router.get("/items")
def list_items(
    category_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    branch_id: Optional[str] = None,  # accepted for future branch scoping
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Returns a list of menu items for POS.
    Shape matches what the Flutter MenuItem.fromJson() expects.
    """
    # tenant isolation
    if tenant_id is not None and tenant_id != ctx.tenant_id:
        # pretend nothing found if trying to peek at another tenant
        return []

    # if caller/ctx has a branch, enforce via category join
    q = (
        db.query(MenuItem)
          .join(MenuCategory, MenuCategory.id == MenuItem.category_id)
          .filter(MenuItem.deleted_at.is_(None))
          .filter(MenuItem.tenant_id == ctx.tenant_id)
    )

    # enforce branch either from ctx or provided (validated)
    if ctx.branch_id or (branch_id or "").strip():
        eff_branch = _effective_branch_id(db, ctx, branch_id)
        q = q.filter(MenuCategory.branch_id == eff_branch)

    # category filter if provided (and ensure it's in-tenant/branch)
    if category_id:
        cat = _ensure_category_access(db, category_id, ctx)
        q = q.filter(MenuItem.category_id == cat.id)

    rows: List[MenuItem] = q.all()
    return [_item_payload(m) for m in rows]


@router.post("/items", response_model=MenuItemOut)
def create_item(
    body: MenuItemIn,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Create a new item.
    Used by: repo.createItem(...)
    """
    # must attach to a category that belongs to this tenant/branch
    _ensure_category_access(db, body.category_id, ctx)

    data = body.model_dump()
    # force tenant to caller's tenant
    data["tenant_id"] = ctx.tenant_id

    it = MenuItem(**data)
    db.add(it)
    db.commit()
    db.refresh(it)
    return MenuItemOut(id=it.id, **body.model_dump() | {"tenant_id": ctx.tenant_id})


# NEW: PATCH /items/{item_id}
@router.patch("/items/{item_id}")
def patch_item(
    item_id: str,
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Update editable fields on an existing item.
    Used by the Item Detail page (name, desc, active, stock_out, taxInclusive, gstRate, etc.)
    """
    it = _ensure_item_access(db, item_id, ctx)

    # if category is changing, validate new category belongs to same tenant/branch scope
    if "category_id" in body and body["category_id"]:
        _ensure_category_access(db, str(body["category_id"]), ctx)

    # fields we allow patching from the UI
    updatable_fields = [
        "name",
        "description",
        "category_id",
        "sku",
        "hsn",
        "is_active",
        "stock_out",
        "tax_inclusive",
        "gst_rate",
        "kitchen_station_id",
    ]
    for fld in updatable_fields:
        if fld in body:
            setattr(it, fld, body[fld])

    db.commit()
    db.refresh(it)
    return _item_payload(it)


@router.delete("/items/{item_id}")
def delete_item(
    item_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Soft-delete an item by setting deleted_at.
    Frontend calls catalogRepo.deleteItem(id).
    """
    it = _ensure_item_access(db, item_id, ctx)
    it.deleted_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": item_id}


@router.post("/items/{item_id}/stock_out")
def set_stock_out(
    item_id: str,
    value: bool,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    it = _ensure_item_access(db, item_id, ctx)
    it.stock_out = bool(value)
    db.commit()
    return {"id": it.id, "stock_out": it.stock_out}


@router.post("/items/{item_id}/assign_station")
def assign_station(
    item_id: str,
    station_id: str | None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    it = _ensure_item_access(db, item_id, ctx)
    it.kitchen_station_id = station_id
    db.commit()
    return {"id": it.id, "kitchen_station_id": it.kitchen_station_id}


@router.post("/items/{item_id}/update_tax")
def update_tax(
    item_id: str,
    gst_rate: float,
    tax_inclusive: bool = True,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Keeps backward-compat with existing frontend which calls updateItemTax().
    Newer UI may just PATCH /items/{id}, but we keep this endpoint too.
    """
    it = _ensure_item_access(db, item_id, ctx)
    it.gst_rate = gst_rate
    it.tax_inclusive = tax_inclusive
    db.commit()
    return {
        "id": it.id,
        "gst_rate": float(it.gst_rate),
        "tax_inclusive": bool(it.tax_inclusive),
    }


# ---------- VARIANTS ----------

@router.get("/variants")
def list_variants(
    item_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Returns all variants for a given item_id.
    Matches ItemVariant.fromJson() in Flutter.
    """
    _ensure_item_access(db, item_id, ctx)
    rows: List[ItemVariant] = (
        db.query(ItemVariant)
        .filter(ItemVariant.item_id == item_id)
        .order_by(ItemVariant.is_default.desc(), ItemVariant.label.asc())
        .all()
    )
    return [_variant_payload(v) for v in rows]


@router.post("/variants", response_model=VariantOut)
def create_variant(
    body: VariantIn,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Create a variant. If is_default=True, unset any existing default for that item.
    Used by ManageVariantsSheet 'Add' button.
    """
    data = body.model_dump()

    item_id = data.get("item_id")
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id required")

    # ensure variant is for an item in my tenant/branch
    _ensure_item_access(db, item_id, ctx)

    make_default = bool(data.get("is_default", False))
    if make_default:
        db.query(ItemVariant).filter(
            ItemVariant.item_id == item_id
        ).update({"is_default": False})

    v = ItemVariant(**data)
    db.add(v)
    db.commit()
    db.refresh(v)
    return VariantOut(id=v.id, **data)


# NEW: PATCH /variants/{variant_id}
@router.patch("/variants/{variant_id}")
def update_variant(
    variant_id: str,
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Edit label / prices / default flag for a variant.
    Called by ManageVariantsSheet -> 'Edit'.
    """
    v: ItemVariant | None = db.get(ItemVariant, variant_id)
    if not v:
        raise HTTPException(status_code=404, detail="variant not found")

    # tenant/branch scope via the parent item
    _ensure_item_access(db, v.item_id, ctx)

    # if caller wants to make THIS variant default, unset all others first
    if body.get("is_default") is True:
        db.query(ItemVariant).filter(
            ItemVariant.item_id == v.item_id
        ).update({"is_default": False})

    # allowed fields to patch
    for fld in ["label", "mrp", "base_price", "is_default"]:
        if fld in body:
            setattr(v, fld, body[fld])

    db.commit()
    db.refresh(v)
    return _variant_payload(v)


# NEW: DELETE /variants/{variant_id}
@router.delete("/variants/{variant_id}")
def delete_variant(
    variant_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Delete variant. If it was default, promote another variant as default.
    Called by ManageVariantsSheet -> trash icon.
    """
    v: ItemVariant | None = db.get(ItemVariant, variant_id)
    if not v:
        raise HTTPException(status_code=404, detail="variant not found")

    _ensure_item_access(db, v.item_id, ctx)

    item_id = v.item_id
    was_default = bool(v.is_default)

    db.delete(v)
    db.commit()

    if was_default:
        # pick another variant and mark it default
        others: List[ItemVariant] = (
            db.query(ItemVariant)
            .filter(ItemVariant.item_id == item_id)
            .order_by(ItemVariant.label.asc())
            .all()
        )
        if others:
            others[0].is_default = True
            db.commit()

    return {"ok": True, "id": variant_id}


# ---------- CATEGORIES ----------

@router.post("/categories", response_model=MenuCategoryOut)
def create_category(
    body: MenuCategoryIn,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    # normalize & enforce tenant/branch
    eff_branch = _effective_branch_id(db, ctx, body.branch_id)
    data = body.model_dump()
    data["tenant_id"] = ctx.tenant_id
    data["branch_id"] = eff_branch

    cat = MenuCategory(**data)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return MenuCategoryOut(id=cat.id, **data)


@router.get("/categories", response_model=List[MenuCategoryOut])
def list_categories(
    tenant_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    # tenant isolation — ignore mismatched query attempts
    if tenant_id != ctx.tenant_id:
        return []

    eff_branch = _effective_branch_id(db, ctx, branch_id)

    rows = (
        db.query(MenuCategory)
        .filter(
            MenuCategory.tenant_id == ctx.tenant_id,
            MenuCategory.branch_id == eff_branch,
            MenuCategory.deleted_at.is_(None),
        )
        .order_by(MenuCategory.position)
        .all()
    )
    return [
        MenuCategoryOut(
            id=r.id,
            tenant_id=r.tenant_id,
            branch_id=r.branch_id,
            name=r.name,
            position=r.position,
        )
        for r in rows
    ]


# NEW: PATCH /categories/{cat_id}
@router.patch("/categories/{cat_id}", response_model=MenuCategoryOut)
def patch_category(
    cat_id: str,
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Update a category's name / position.
    Used by Edit Category dialog.
    """
    cat = _ensure_category_access(db, cat_id, ctx)

    if "name" in body:
        cat.name = body["name"]
    if "position" in body:
        cat.position = body["position"]

    db.commit()
    db.refresh(cat)
    return MenuCategoryOut(
        id=cat.id,
        tenant_id=cat.tenant_id,
        branch_id=cat.branch_id,
        name=cat.name,
        position=cat.position,
    )


@router.delete("/categories/{cat_id}")
def delete_category(
    cat_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Soft delete: set deleted_at timestamp.
    Frontend calls catalogRepo.deleteCategory(id).
    """
    cat = _ensure_category_access(db, cat_id, ctx)
    cat.deleted_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": cat_id}


# ---------- MODIFIERS / GROUPS ----------

@router.post("/modifier_groups")
def create_modifier_group(
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    body: {tenant_id: str, name: str, min_sel: int, max_sel: int}
    """
    data = dict(body)
    data["tenant_id"] = ctx.tenant_id  # force to caller's tenant
    mg = ModifierGroup(**data)
    db.add(mg)
    db.commit()
    db.refresh(mg)
    return {"id": mg.id}


@router.post("/modifiers")
def create_modifier(
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    body: {group_id: str, name: str, price_delta: float}
    """
    group_id = body.get("group_id")
    grp: ModifierGroup | None = db.get(ModifierGroup, group_id)
    if not grp or grp.tenant_id != ctx.tenant_id:
        raise HTTPException(404, detail="modifier group not found")
    m = Modifier(**body)
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"id": m.id}


@router.get("/items/{item_id}/modifiers_full")
def get_item_modifiers_full(
    item_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Return all modifier groups linked to this item AND the modifiers in each group.

    Shape is friendly for Flutter:
    [
      {
        "group_id": "...",
        "name": "Toppings",
        "required": false,
        "min_sel": 0,
        "max_sel": 3,
        "modifiers": [
          {"id": "...", "name": "Extra Cheese", "price_delta": 20.0},
          {"id": "...", "name": "No Onion", "price_delta": 0.0},
          ...
        ]
      },
      ...
    ]
    """
    # enforce item access
    _ensure_item_access(db, item_id, ctx)

    # find modifier groups linked to this item and scoped to tenant
    groups: List[ModifierGroup] = (
        db.query(ModifierGroup)
        .join(
            ItemModifierGroup,
            ItemModifierGroup.group_id == ModifierGroup.id,
        )
        .filter(
            ItemModifierGroup.item_id == item_id,
            ModifierGroup.tenant_id == ctx.tenant_id,
        )
        .all()
    )

    result = []
    for g in groups:
        # load all modifiers in that group
        mods: List[Modifier] = (
            db.query(Modifier)
            .filter(Modifier.group_id == g.id)
            .all()
        )

        result.append(
            {
                "group_id": g.id,
                "name": g.name,
                "required": bool(getattr(g, "required", False)),
                "min_sel": getattr(g, "min_sel", 0) or 0,
                "max_sel": getattr(g, "max_sel", None),
                "modifiers": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "price_delta": _as_float(m.price_delta) or 0.0,
                    }
                    for m in mods
                ],
            }
        )

    return result


@router.post("/items/{item_id}/modifier_groups")
def link_item_group(
    item_id: str,
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    body: {group_id: str}
    """
    # item must be in my tenant/branch
    _ensure_item_access(db, item_id, ctx)

    group_id = (body or {}).get("group_id")
    grp: ModifierGroup | None = db.get(ModifierGroup, group_id)
    if not grp or grp.tenant_id != ctx.tenant_id:
        raise HTTPException(404, detail="modifier group not found")

    exists = (
        db.query(ItemModifierGroup)
        .filter(
            ItemModifierGroup.item_id == item_id,
            ItemModifierGroup.group_id == group_id,
        )
        .first()
    )
    if not exists:
        link = ItemModifierGroup(item_id=item_id, group_id=group_id)
        db.add(link)
        db.commit()
        return {"ok": True, "linked": True}
    return {"ok": True, "linked": False}
