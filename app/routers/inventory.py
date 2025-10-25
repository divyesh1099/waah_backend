# app/routers/inventory.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date
from decimal import Decimal

from app.db import get_db
from app.deps import require_auth, require_perm
from app.models.core import (
    Ingredient,
    RecipeBOM,
    StockMove,
    StockMoveType,
    Purchase,
    PurchaseLine,
    ReportStockSnapshot,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])


# ---------- helpers (NEW) ----------

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


# ---------- INGREDIENTS ----------

@router.post("/ingredients")
def add_ingredient(
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    body = { "tenant_id": "...", "name": "...", "uom": "kg", "min_level": 2.5 }
    """
    i = Ingredient(**body)
    db.add(i)
    db.commit()
    db.refresh(i)
    return {"id": i.id}


# NEW: list all ingredients with qty_on_hand & min_level
@router.get("/ingredients")
def list_ingredients(
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    sub: str = Depends(require_auth),
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
    q = db.query(Ingredient)
    if tenant_id is not None:
        q = q.filter(Ingredient.tenant_id == tenant_id)

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


# NEW: patch/update ingredient (min_level, name, uom)
@router.patch("/ingredients/{ingredient_id}")
def update_ingredient(
    ingredient_id: str,
    body: dict,
    db: Session = Depends(get_db),
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    body could be { "min_level": 5.0 } or { "name": "Roma Tomatoes" } etc.
    Used by InventoryPage min-level edit dialog.
    """
    ing = db.get(Ingredient, ingredient_id)
    if not ing:
        raise HTTPException(status_code=404, detail="ingredient not found")

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
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
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
    db.query(RecipeBOM).filter(
        RecipeBOM.item_id == body["item_id"]
    ).delete()

    for line in body.get("lines", []):
        db.add(
            RecipeBOM(
                item_id=body["item_id"],
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
    sub: str = Depends(require_perm("SETTINGS_EDIT")),
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
        tenant_id=body["tenant_id"],
        supplier=body.get("supplier"),
        note=body.get("note"),
    )
    db.add(p)
    db.flush()  # get p.id without full commit

    for l in body.get("lines", []):
        db.add(
            PurchaseLine(
                purchase_id=p.id,
                ingredient_id=l["ingredient_id"],
                qty=l["qty"],
                unit_cost=l["unit_cost"],
            )
        )
        db.add(
            StockMove(
                ingredient_id=l["ingredient_id"],
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
    sub: str = Depends(require_auth),
):
    """
    Returns ingredients where current qty_on_hand <= min_level.
    Used by dashboard / alert UI.
    """
    sums = (
        db.query(
            StockMove.ingredient_id,
            func.coalesce(func.sum(StockMove.qty_change), 0),
        )
        .group_by(StockMove.ingredient_id)
        .all()
    )
    levels = {ing_id: float(qty) for ing_id, qty in sums}

    res = []
    for ing in db.query(Ingredient).all():
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
    sub: str = Depends(require_auth),
):
    """
    Pull a precomputed snapshot row for each ingredient for `day`
    from ReportStockSnapshot instead of calculating live.
    """
    rows = (
        db.query(ReportStockSnapshot, Ingredient.name)
        .join(Ingredient, Ingredient.id == ReportStockSnapshot.ingredient_id)
        .filter(ReportStockSnapshot.at_date == day)
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
