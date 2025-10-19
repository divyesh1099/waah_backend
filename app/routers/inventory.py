# app/routers/inventory.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import date
from app.db import get_db
from app.deps import require_auth, require_perm
from app.models.core import (
    Ingredient,
    RecipeBOM, StockMove, StockMoveType, Purchase, PurchaseLine,  # keep existing imports used by other endpoints
    ReportStockSnapshot,  # NEW
)

router = APIRouter(prefix="/inventory", tags=["inventory"])

@router.post("/ingredients")
def add_ingredient(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    i = Ingredient(**body); db.add(i); db.commit(); db.refresh(i)
    return {"id": i.id}

@router.post("/recipe")
def set_recipe(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    db.query(RecipeBOM).filter(RecipeBOM.item_id==body["item_id"]).delete()
    for line in body.get("lines", []):
        db.add(RecipeBOM(item_id=body["item_id"], ingredient_id=line["ingredient_id"], qty=line["qty"]))
    db.commit()
    return {"ok": True}

@router.post("/purchase")
def purchase(body: dict, db: Session = Depends(get_db), sub: str = Depends(require_perm("SETTINGS_EDIT"))):
    p = Purchase(tenant_id=body["tenant_id"], supplier=body.get("supplier"), note=body.get("note"))
    db.add(p); db.flush()
    for l in body.get("lines", []):
        db.add(PurchaseLine(purchase_id=p.id, ingredient_id=l["ingredient_id"], qty=l["qty"], unit_cost=l["unit_cost"]))
        db.add(StockMove(ingredient_id=l["ingredient_id"], type=StockMoveType.PURCHASE, qty_change=l["qty"], reason=f"Purchase {p.id}", ref_purchase_id=p.id))
    db.commit(); db.refresh(p)
    return {"purchase_id": p.id}

@router.get("/low_stock")
def low_stock(db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    from sqlalchemy import func
    sums = db.query(StockMove.ingredient_id, func.coalesce(func.sum(StockMove.qty_change),0)).group_by(StockMove.ingredient_id).all()
    levels = {ing_id: float(qty) for ing_id, qty in sums}
    res = []
    for ing in db.query(Ingredient).all():
        qty = levels.get(ing.id, 0.0)
        if qty <= float(ing.min_level or 0):
            res.append({"ingredient_id": ing.id, "name": ing.name, "qty": qty, "min_level": float(ing.min_level or 0)})
    return res

# REPLACED: now reads precomputed snapshot instead of computing on-the-fly
@router.get("/stock_report")
def stock_report(day: date, db: Session = Depends(get_db), sub: str = Depends(require_auth)):
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
