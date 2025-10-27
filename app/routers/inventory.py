# app/routers/inventory.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date
from decimal import Decimal
from typing import List

from app.db import get_db
from app.deps import AuthCtx, require_auth, require_perm
from app.models.core import (
    Ingredient,
    RecipeBOM,
    StockMove,
    StockMoveType,
    Purchase,
    PurchaseLine,
    ReportStockSnapshot,
    MenuItem,
    MenuCategory,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])


# ---------- helpers ----------

def _to_float(val):
    if val is None:
        return 0.0
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(val)
    except Exception:
        return 0.0

def _qty_on_hand(db: Session, ingredient_id: str) -> float:
    total = (
        db.query(func.coalesce(func.sum(StockMove.qty_change), 0))
        .filter(StockMove.ingredient_id == ingredient_id)
        .scalar()
    )
    if total is None:
        return 0.0
    if isinstance(total, Decimal):
        return float(total)
    return float(total)

def _ensure_ingredient_access(db: Session, ingredient_id: str, ctx: AuthCtx) -> Ingredient:
    ing = db.get(Ingredient, ingredient_id)
    if not ing or ing.tenant_id != ctx.tenant_id:
        # hide cross-tenant data
        raise HTTPException(status_code=404, detail="ingredient not found")
    return ing

def _ensure_item_access(db: Session, item_id: str, ctx: AuthCtx) -> MenuItem:
    """
    Same branch/tenant rules as /menu:
      - Item must be in caller's tenant
      - Branch enforced via item's category (MenuItem has no branch_id)
    """
    it = db.get(MenuItem, item_id)
    if not it or it.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="item not found")
    cat = db.get(MenuCategory, it.category_id)
    if (not cat) or cat.tenant_id != ctx.tenant_id or (ctx.branch_id and cat.branch_id != ctx.branch_id):
        raise HTTPException(status_code=404, detail="item not found")
    return it


# ---------- INGREDIENTS ----------

@router.post("/ingredients")
def add_ingredient(
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    body = { "tenant_id": "...", "name": "...", "uom": "kg", "min_level": 2.5 }
    """
    data = dict(body)
    data["tenant_id"] = ctx.tenant_id  # force to caller's tenant
    i = Ingredient(**data)
    db.add(i)
    db.commit()
    db.refresh(i)
    return {"id": i.id}


# list all ingredients with qty_on_hand & min_level
@router.get("/ingredients")
def list_ingredients(
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Returns [
      {
        "id": "...",
        "tenant_id": "...",
        "name": "Tomatoes",
        "uom": "kg",
        "min_level": 2.000,
        "qty_on_hand": 5.500
      },
      ...
    ]
    qty_on_hand is derived from StockMove.
    """
    # tenant isolation: ignore mismatched tenant_id hints
    q = db.query(Ingredient).filter(Ingredient.tenant_id == ctx.tenant_id)

    out = []
    for ing in q.order_by(Ingredient.name.asc()).all():
        out.append({
            "id": ing.id,
            "tenant_id": ing.tenant_id,
            "name": ing.name,
            "uom": ing.uom,
            "min_level": _to_float(getattr(ing, "min_level", 0)),
            "qty_on_hand": _qty_on_hand(db, ing.id),
        })
    return out


# patch/update ingredient (min_level, name, uom)
@router.patch("/ingredients/{ingredient_id}")
def update_ingredient(
    ingredient_id: str,
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    body could be { "min_level": 5.0 } or { "name": "Roma Tomatoes" } etc.
    Used by InventoryPage min-level edit dialog.
    """
    ing = _ensure_ingredient_access(db, ingredient_id, ctx)

    for fld in ["name", "uom", "min_level"]:
        if fld in body:
            setattr(ing, fld, body[fld])

    db.commit()
    db.refresh(ing)

    return {
        "id": ing.id,
        "tenant_id": ing.tenant_id,
        "name": ing.name,
        "uom": ing.uom,
        "min_level": _to_float(getattr(ing, "min_level", 0)),
        "qty_on_hand": _qty_on_hand(db, ing.id),
    }


# ---------- RECIPES / BOM ----------

@router.post("/recipe")
def set_recipe(
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    body = {
        "item_id": "...",
        "lines": [
            {"ingredient_id": "...", "qty": 0.150},
            ...
        ]
    }
    """
    item_id = body.get("item_id")
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id required")

    # Enforce tenant + branch via item->category
    _ensure_item_access(db, item_id, ctx)

    # Validate each ingredient belongs to tenant
    lines = body.get("lines", [])
    for line in lines:
        _ensure_ingredient_access(db, line["ingredient_id"], ctx)

    # Replace BOM
    db.query(RecipeBOM).filter(RecipeBOM.item_id == item_id).delete()
    for line in lines:
        db.add(
            RecipeBOM(
                item_id=item_id,
                ingredient_id=line["ingredient_id"],
                qty=line["qty"],
            )
        )

    db.commit()
    return {"ok": True}


# ---------- PURCHASE / STOCK IN ----------

@router.post("/purchase")
def purchase(
    body: dict,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    body = {
      "tenant_id": "...",
      "supplier": "...",
      "note": "...",
      "lines": [
        {"ingredient_id": "...", "qty": 2.5, "unit_cost": 100.0},
        ...
      ]
    }

    For each line:
    - create PurchaseLine
    - create StockMove (type=PURCHASE, +qty_change)
    """
    p = Purchase(
        tenant_id=ctx.tenant_id,  # force to caller's tenant
        supplier=body.get("supplier"),
        note=body.get("note"),
    )
    db.add(p)
    db.flush()  # get p.id without full commit

    for l in body.get("lines", []):
        # Enforce ingredient belongs to tenant
        ing = _ensure_ingredient_access(db, l["ingredient_id"], ctx)

        db.add(
            PurchaseLine(
                purchase_id=p.id,
                ingredient_id=ing.id,
                qty=l["qty"],
                unit_cost=l["unit_cost"],
            )
        )
        db.add(
            StockMove(
                ingredient_id=ing.id,
                type=StockMoveType.PURCHASE,
                qty_change=l["qty"],
                reason=f"Purchase {p.id}",
                ref_purchase_id=p.id,
            )
        )

    db.commit()
    db.refresh(p)
    return {"purchase_id": p.id}


# ---------- LOW STOCK ALERT ----------

@router.get("/low_stock")
def low_stock(
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Returns ingredients where current qty_on_hand <= min_level.
    Used by dashboard / alert UI.
    """
    # Sum quantities only for this tenant's ingredients
    sums = (
        db.query(
            StockMove.ingredient_id,
            func.coalesce(func.sum(StockMove.qty_change), 0),
        )
        .join(Ingredient, Ingredient.id == StockMove.ingredient_id)
        .filter(Ingredient.tenant_id == ctx.tenant_id)
        .group_by(StockMove.ingredient_id)
        .all()
    )
    levels = {ing_id: float(qty) for ing_id, qty in sums}

    res = []
    for ing in db.query(Ingredient).filter(Ingredient.tenant_id == ctx.tenant_id).all():
        qty = levels.get(ing.id, 0.0)
        min_level = float(ing.min_level or 0)
        if qty <= min_level:
            res.append({
                "ingredient_id": ing.id,
                "name": ing.name,
                "qty": qty,
                "min_level": min_level,
            })
    return res


# ---------- STOCK SNAPSHOT REPORT (precomputed) ----------

@router.get("/stock_report")
def stock_report(
    day: date,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Pull a precomputed snapshot row for each ingredient for `day`
    from ReportStockSnapshot instead of calculating live.
    """
    rows = (
        db.query(ReportStockSnapshot, Ingredient.name)
        .join(Ingredient, Ingredient.id == ReportStockSnapshot.ingredient_id)
        .filter(ReportStockSnapshot.at_date == day, Ingredient.tenant_id == ctx.tenant_id)
        .order_by(Ingredient.name.asc())
        .all()
    )

    out = []
    for snap, name in rows:
        out.append({
            "ingredient_id": snap.ingredient_id,
            "name": name,
            "opening": float(snap.opening_qty or 0),
            "purchased": float(snap.purchased_qty or 0),
            "used": float(snap.used_qty or 0),
            "closing": float(snap.closing_qty or 0),
        })
    return out
