from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File,
)
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
import csv
import io

from app.db import get_db
from app.schemas.menu import (
    MenuCategoryIn,
    MenuCategoryOut,
    MenuItemIn,
    MenuItemOut,
    VariantIn,
    VariantOut,
    BulkMenuIn,
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
from app.util.media import save_image_upload

router = APIRouter(prefix="/menu", tags=["menu"])


# ---------- helpers ----------

def _as_float(val: Decimal | float | int | None) -> float | None:
    if val is None:
        return None
    return float(val)


def _ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # Send ISO8601 that Flutter DateTime.tryParse() can parse
    return dt.isoformat()


def _item_payload(m: MenuItem) -> dict:
    """Consistent shape for MenuItem back to Flutter (MenuItem.fromJson)."""
    return {
        "id": m.id,
        "tenant_id": m.tenant_id,
        # NOTE: MenuItem model doesn't expose branch_id directly in Flutter.
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
        "image_url": getattr(m, "image_url", None),
        "created_at": _ts(getattr(m, "created_at", None)),
        "updated_at": _ts(getattr(m, "updated_at", None)),
    }


def _variant_payload(v: ItemVariant) -> dict:
    """Matches ItemVariant.fromJson in Flutter."""
    return {
        "id": v.id,
        "item_id": v.item_id,
        "label": v.label,
        "mrp": _as_float(v.mrp),
        "base_price": _as_float(v.base_price) or 0.0,
        "is_default": bool(v.is_default),
        "image_url": getattr(v, "image_url", None),
    }


def _category_payload(c: MenuCategory) -> dict:
    """Matches MenuCategory.fromJson in Flutter."""
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "branch_id": c.branch_id,
        "name": c.name,
        "position": c.position,
        "created_at": _ts(getattr(c, "created_at", None)),
        "updated_at": _ts(getattr(c, "updated_at", None)),
    }


def _modifier_payload(md: Modifier) -> dict:
    return {
        "id": md.id,
        "group_id": md.group_id,
        "name": md.name,
        "price_delta": _as_float(md.price_delta) or 0.0,
    }


def _modifier_group_block(
    grp: ModifierGroup,
    modifiers: List[Modifier],
) -> dict:
    """Shape returned by GET /items/{item_id}/modifiers_full."""
    return {
        "id": grp.id,
        "tenant_id": grp.tenant_id,
        "name": grp.name,
        "min_sel": grp.min_sel,
        "max_sel": grp.max_sel,
        "required": bool(grp.required),
        "modifiers": [
            _modifier_payload(m) for m in modifiers
        ],
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
    if (
        (not cat)
        or cat.tenant_id != ctx.tenant_id
        or (ctx.branch_id and cat.branch_id != ctx.branch_id)
    ):
        raise HTTPException(status_code=404, detail="item not found")
    return it


def _effective_branch_id(
    db: Session,
    ctx: AuthCtx,
    provided_branch_id: Optional[str],
) -> str:
    """
    Decide which branch to use:
      - Caller-provided branch_id (query/body) wins if present.
      - Otherwise fall back to branch_id embedded in the auth token.
      - In either case, ensure the branch belongs to the caller's tenant.
    """
    requested_bid = (provided_branch_id or "").strip()
    ctx_bid = (ctx.branch_id or "").strip()
    bid = requested_bid or ctx_bid
    if not bid:
        raise HTTPException(status_code=400, detail="branch not selected")
    br = db.get(Branch, bid)
    if (not br) or br.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="branch not found")
    return bid


async def _save_upload_and_get_url(
    subdir: str,
    obj_id: str,
    upload: UploadFile,
) -> str:
    """
    Save an UploadFile under storage backend and return a URL that can be stored in image_url.
    """
    return await save_image_upload(upload, subdir=f"{subdir}/{obj_id}")


# -----------------------------------------------------------------------------
# CATEGORIES
# -----------------------------------------------------------------------------

@router.get("/categories")
def list_categories(
    tenant_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Returns categories for the current tenant/branch.
    Matches CatalogRepo.loadCategories() / fetchCategories().
    """
    if tenant_id is not None and tenant_id != ctx.tenant_id:
        # hide cross-tenant attempts
        return []

    q = (
        db.query(MenuCategory)
        .filter(MenuCategory.deleted_at.is_(None))
        .filter(MenuCategory.tenant_id == ctx.tenant_id)
    )

    # apply branch scoping (via ctx or explicit)
    if ctx.branch_id or (branch_id or "").strip():
        eff_branch = _effective_branch_id(db, ctx, branch_id)
        q = q.filter(MenuCategory.branch_id == eff_branch)

    rows: List[MenuCategory] = (
        q.order_by(MenuCategory.position.asc(), MenuCategory.name.asc()).all()
    )

    return [_category_payload(c) for c in rows]


@router.post("/categories", response_model=MenuCategoryOut)
def create_category(
    body: MenuCategoryIn,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Create a category. CatalogRepo.createCategory() calls this.
    We'll force tenant_id and branch_id to match caller scope.
    """
    # figure out which branch this should live in
    eff_branch = _effective_branch_id(
        db,
        ctx,
        getattr(body, "branch_id", None),
    )

    data = body.model_dump()
    data["tenant_id"] = ctx.tenant_id
    data["branch_id"] = eff_branch

    c = MenuCategory(**data)
    db.add(c)
    db.commit()
    db.refresh(c)

    out = _category_payload(c)
    return MenuCategoryOut(**out)


@router.patch("/categories/{category_id}")
def patch_category(
    category_id: str,
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Edit a category's name / position.
    CatalogRepo.updateCategory() calls this via ApiClient.updateCategory().
    """
    c = _ensure_category_access(db, category_id, ctx)

    allowed = ["name", "position"]
    for fld in allowed:
        if fld in body:
            setattr(c, fld, body[fld])

    db.commit()
    db.refresh(c)
    return _category_payload(c)


@router.delete("/categories/{category_id}")
def delete_category(
    category_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Soft-delete a category.
    CatalogRepo.deleteCategory() calls this.
    """
    c = _ensure_category_access(db, category_id, ctx)
    c.deleted_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": category_id}


# -----------------------------------------------------------------------------
# ITEMS
# -----------------------------------------------------------------------------

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
    import sys
    import traceback
    try:
        # tenant isolation
        if tenant_id is not None and tenant_id != ctx.tenant_id:
            # pretend nothing found if trying to peek at another tenant
            return []

        # base query
        q = (
            db.query(MenuItem)
            .join(MenuCategory, MenuCategory.id == MenuItem.category_id)
            .filter(MenuItem.deleted_at.is_(None))
            .filter(MenuItem.tenant_id == ctx.tenant_id)
        )

        # enforce branch via category.branch_id
        if ctx.branch_id or (branch_id or "").strip():
            eff_branch = _effective_branch_id(db, ctx, branch_id)
            q = q.filter(MenuCategory.branch_id == eff_branch)

        # filter by category if provided
        if category_id:
            _ensure_category_access(db, category_id, ctx)
            q = q.filter(MenuItem.category_id == category_id)

        rows: List[MenuItem] = q.all()
        return [_item_payload(m) for m in rows]
    except Exception as e:
        print("CRASH IN LIST_ITEMS (FULL):", file=sys.stdout)
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        raise e


@router.post("/items", response_model=MenuItemOut)
def create_item(
    body: MenuItemIn,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Create a new item.
    Used by: CatalogRepo.createItem().
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


@router.patch("/items/{item_id}")
def patch_item(
    item_id: str,
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Update editable fields on an existing item.
    Used by Item Detail editor (CatalogRepo.updateItem()).
    """
    it = _ensure_item_access(db, item_id, ctx)

    # If category is changing, validate target category is still in-scope.
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
        "image_url",  # let PATCH set/clear if you already uploaded
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
    station_id: Optional[str] = None,
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
    Newer UI may just PATCH /items/{id}.
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


# -----------------------------------------------------------------------------
# VARIANTS
# -----------------------------------------------------------------------------

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
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
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


@router.patch("/variants/{variant_id}")
def update_variant(
    variant_id: str,
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Edit label / prices / default flag / image_url for a variant.
    Called by ManageVariantsSheet -> 'Edit'.
    """
    v: ItemVariant | None = db.get(ItemVariant, variant_id)
    if not v:
        raise HTTPException(status_code=404, detail="variant not found")

    # tenant/branch scope via the parent item
    _ensure_item_access(db, v.item_id, ctx)

    # If caller wants to make THIS variant default, unset all others first.
    if body.get("is_default") is True:
        db.query(ItemVariant).filter(
            ItemVariant.item_id == v.item_id
        ).update({"is_default": False})

    allowed_fields = [
        "label",
        "mrp",
        "base_price",
        "is_default",
        "image_url",
    ]
    for fld in allowed_fields:
        if fld in body:
            setattr(v, fld, body[fld])

    db.commit()
    db.refresh(v)
    return _variant_payload(v)


@router.delete("/variants/{variant_id}")
def delete_variant(
    variant_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Hard-delete or soft-delete a variant.
    CatalogRepo.deleteVariant() calls this.
    """
    v: ItemVariant | None = db.get(ItemVariant, variant_id)
    if not v:
        # deleting a missing variant shouldn't explode in UI
        return {"ok": True, "id": variant_id}

    _ensure_item_access(db, v.item_id, ctx)

    # If you prefer soft delete, add deleted_at instead of delete().
    db.delete(v)
    db.commit()
    return {"ok": True, "id": variant_id}


# -----------------------------------------------------------------------------
# MODIFIER GROUPS / MODIFIERS
# -----------------------------------------------------------------------------

@router.post("/modifier_groups")
def create_modifier_group(
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Create a ModifierGroup.
    CatalogRepo.createModifierGroup() calls this with ModifierGroup.toJson().
    Expected body keys:
      tenant_id (ignored/overridden),
      name,
      min_sel,
      max_sel,
      required
    """
    # force it into the current tenant
    mg = ModifierGroup(
        tenant_id=ctx.tenant_id,
        name=body.get("name", ""),
        min_sel=body.get("min_sel", 0),
        max_sel=body.get("max_sel"),
        required=bool(body.get("required", False)),
    )
    db.add(mg)
    db.commit()
    db.refresh(mg)

    return {
        "id": mg.id,
        "tenant_id": mg.tenant_id,
        "name": mg.name,
        "min_sel": mg.min_sel,
        "max_sel": mg.max_sel,
        "required": bool(mg.required),
    }


@router.post("/modifiers")
def create_modifier(
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Create a Modifier option inside a ModifierGroup.
    CatalogRepo.createModifier() calls this with Modifier.toJson().
    Expected body keys:
      group_id,
      name,
      price_delta
    """
    group_id = body.get("group_id")
    if not group_id:
        raise HTTPException(status_code=400, detail="group_id required")

    grp = db.get(ModifierGroup, group_id)
    if not grp or grp.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="modifier group not found")

    mod = Modifier(
        group_id=group_id,
        name=body.get("name", ""),
        price_delta=body.get("price_delta", 0),
    )
    db.add(mod)
    db.commit()
    db.refresh(mod)

    return _modifier_payload(mod)


@router.post("/items/{item_id}/modifier_groups")
def link_item_modifier_group(
    item_id: str,
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Link a ModifierGroup to an item.
    CatalogRepo.linkItemModifierGroup() posts { "group_id": "..."}.
    """
    it = _ensure_item_access(db, item_id, ctx)

    group_id = body.get("group_id")
    if not group_id:
        raise HTTPException(status_code=400, detail="group_id required")

    grp = db.get(ModifierGroup, group_id)
    if not grp or grp.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="modifier group not found")

    # ensure not already linked
    existing = (
        db.query(ItemModifierGroup)
        .filter(
            ItemModifierGroup.item_id == it.id,
            ItemModifierGroup.group_id == grp.id,
        )
        .first()
    )
    if not existing:
        link = ItemModifierGroup(item_id=it.id, group_id=grp.id)
        db.add(link)
        db.commit()

    return {"ok": True, "item_id": it.id, "group_id": grp.id}


@router.get("/items/{item_id}/modifiers_full")
def get_item_modifier_groups_full(
    item_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Return all modifier groups (and their modifiers[]) attached to this item.
    ApiClient.fetchItemModifierGroups() uses this.
    """
    it = _ensure_item_access(db, item_id, ctx)

    # find all groups linked to this item
    groups: List[ModifierGroup] = (
        db.query(ModifierGroup)
        .join(ItemModifierGroup, ItemModifierGroup.group_id == ModifierGroup.id)
        .filter(ItemModifierGroup.item_id == it.id)
        .filter(ModifierGroup.tenant_id == ctx.tenant_id)
        .all()
    )

    out = []
    for grp in groups:
        mods = (
            db.query(Modifier)
            .filter(Modifier.group_id == grp.id)
            .order_by(Modifier.name.asc())
            .all()
        )
        out.append(_modifier_group_block(grp, mods))

    return out


# -----------------------------------------------------------------------------
# IMAGES (items / variants)
# -----------------------------------------------------------------------------

@router.post("/items/{item_id}/image")
async def upload_item_image(
    item_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Upload an item image. Flutter calls ApiClient.uploadItemImage(), which
    expects { "image_url": "<...>" } back.
    """
    it = _ensure_item_access(db, item_id, ctx)

    img_url = await _save_upload_and_get_url("items", item_id, file)
    it.image_url = img_url
    db.commit()
    return {"image_url": img_url}


@router.delete("/items/{item_id}/image")
def delete_item_image(
    item_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Clear image_url for this item. Flutter calls ApiClient.deleteItemImage().
    """
    it = _ensure_item_access(db, item_id, ctx)

    # optionally also os.remove() the file on disk if you want cleanup.
    # We'll just null out the DB field here.
    it.image_url = None
    db.commit()
    return {"ok": True}

    import logging
    logger = logging.getLogger("uvicorn.error")

    created_cats = 0
    created_items = 0
    created_variants = 0

    try:
        for cat_data in body.categories:
            # Create Category
            new_cat = MenuCategory(
                tenant_id=ctx.tenant_id,
                branch_id=eff_branch,
                name=cat_data.name,
                position=cat_data.position
            )
            db.add(new_cat)
            db.flush() # flush to get new_cat.id

            created_cats += 1
            
            if cat_data.items:
                for item_data in cat_data.items:
                    # Create Item
                    new_item = MenuItem(
                        tenant_id=ctx.tenant_id,
                        category_id=new_cat.id,
                        name=item_data.name,
                        description=item_data.description,
                        sku=item_data.sku,
                        hsn=item_data.hsn,
                        is_active=item_data.is_active,
                        stock_out=item_data.stock_out,
                        tax_inclusive=item_data.tax_inclusive,
                        gst_rate=item_data.gst_rate,
                        kitchen_station_id=item_data.kitchen_station_id,
                    )
                    db.add(new_item)
                    db.flush() # to get new_item.id

                    created_items += 1

                    if item_data.variants:
                        for var_data in item_data.variants:
                            # Create Variant
                            new_var = ItemVariant(
                                item_id=new_item.id,
                                label=var_data.label,
                                base_price=var_data.base_price,
                                mrp=var_data.mrp,
                                is_default=var_data.is_default
                            )
                            db.add(new_var)
                            created_variants += 1
        
        db.commit()
        return {
            "ok": True, 
            "message": f"Inserted {created_cats} categories, {created_items} items, {created_variants} variants.",
            "counts": {
                "categories": created_cats,
                "items": created_items,
                "variants": created_variants
            }
        }

    except Exception as e:
        logger.error(f"Bulk insert failed: {e}")
        db.rollback() 
        raise HTTPException(status_code=400, detail=f"Bulk insert failed: {str(e)}")


@router.post("/variants/{variant_id}/image")
async def upload_variant_image(
    variant_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Upload an image for a specific variant.
    ApiClient.uploadVariantImage() expects { "image_url": "<...>" }.
    """
    v: ItemVariant | None = db.get(ItemVariant, variant_id)
    if not v:
        raise HTTPException(status_code=404, detail="variant not found")

    _ensure_item_access(db, v.item_id, ctx)

    img_url = await _save_upload_and_get_url("variants", variant_id, file)
    v.image_url = img_url
    db.commit()
    return {"image_url": img_url}


@router.delete("/variants/{variant_id}/image")
def delete_variant_image(
    variant_id: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Remove stored image for a variant.
    Flutter calls ApiClient.deleteVariantImage().
    """
    v: ItemVariant | None = db.get(ItemVariant, variant_id)
    if not v:
        return {"ok": True}

    _ensure_item_access(db, v.item_id, ctx)

    v.image_url = None
    db.commit()
    return {"ok": True}


# -----------------------------------------------------------------------------
# BULK CSV UPLOAD (server-side, faster than old client parsing)
# -----------------------------------------------------------------------------

@router.post("/upload-csv")
async def upload_menu_csv(
    file: UploadFile = File(...),
    branch_id: Optional[str] = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Upload a CSV and create categories/items/variants server-side.
    Expected columns (case-insensitive):
    - category (required)
    - name (required)
    - variant_label (required)
    - price (required)
    Optional: description, gst_rate, tax_inclusive, is_active, mrp, sku, hsn, image_url
    """
    # Resolve branch and tenant scope
    eff_branch = _effective_branch_id(db, ctx, branch_id)

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    required = {"category", "name", "variant_label", "price"}
    if not required.issubset(set(headers)):
        missing = required.difference(set(headers))
        raise HTTPException(400, detail=f"Missing required columns: {', '.join(sorted(missing))}")

    # Helpers
    def _to_bool(s: str | None, default: bool = True) -> bool:
        if s is None:
            return default
        v = s.strip().lower()
        if v in ("", "null"):
            return default
        return v in ("1", "true", "yes", "y")

    def _to_float(s: str | None, default: float = 0.0) -> float:
        if s is None or not s.strip():
            return default
        try:
            return float(s)
        except Exception:
            return default

    created_cats = 0
    created_items = 0
    created_vars = 0

    cat_cache: dict[str, MenuCategory] = {}
    item_cache: dict[tuple[str, str], MenuItem] = {}

    for row in reader:
        # Normalize keys
        r = {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        cat_name = (r.get("category") or "").strip()
        item_name = (r.get("name") or "").strip()
        variant_label = (r.get("variant_label") or "").strip()
        price_val = (r.get("price") or "").strip()

        if not cat_name or not item_name or not variant_label or not price_val:
            # skip incomplete rows
            continue

        # Category lookup/create
        cat = cat_cache.get(cat_name.lower())
        if not cat:
            cat = (
                db.query(MenuCategory)
                .filter(
                    MenuCategory.tenant_id == ctx.tenant_id,
                    MenuCategory.branch_id == eff_branch,
                    MenuCategory.name == cat_name,
                    MenuCategory.deleted_at.is_(None),
                )
                .first()
            )
        if not cat:
            cat = MenuCategory(
                tenant_id=ctx.tenant_id,
                branch_id=eff_branch,
                name=cat_name,
                position=0,
            )
            db.add(cat)
            db.flush()
            created_cats += 1
        cat_cache[cat_name.lower()] = cat

        # Item lookup/create by (category, name)
        item_key = (cat.id, item_name.lower())
        it = item_cache.get(item_key)
        if not it:
            it = (
                db.query(MenuItem)
                .filter(
                    MenuItem.tenant_id == ctx.tenant_id,
                    MenuItem.category_id == cat.id,
                    MenuItem.name == item_name,
                    MenuItem.deleted_at.is_(None),
                )
                .first()
            )
        if not it:
            it = MenuItem(
                tenant_id=ctx.tenant_id,
                category_id=cat.id,
                name=item_name,
                description=r.get("description"),
                sku=r.get("sku"),
                hsn=r.get("hsn"),
                is_active=_to_bool(r.get("is_active"), True),
                stock_out=False,
                tax_inclusive=_to_bool(r.get("tax_inclusive"), True),
                gst_rate=_to_float(r.get("gst_rate"), 0.0),
                kitchen_station_id=None,
            )
            db.add(it)
            db.flush()
            created_items += 1
        item_cache[item_key] = it

        # Variant create (always add; no dedup by label to keep simple)
        v = ItemVariant(
            item_id=it.id,
            label=variant_label,
            base_price=_to_float(price_val, 0.0),
            mrp=_to_float(r.get("mrp"), 0.0),
            is_default=False,
        )
        db.add(v)
        created_vars += 1

    db.commit()
    return {
        "ok": True,
        "created": {
            "categories": created_cats,
            "items": created_items,
            "variants": created_vars,
        },
        "branch_id": eff_branch,
        "tenant_id": ctx.tenant_id,
    }
