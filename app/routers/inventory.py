# app/routers/inventory.py
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date
from decimal import Decimal
from typing import List
import csv
from io import StringIO

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
    CashMovement,
    Shift,
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

def _qty_on_hand(db: Session, ingredient_id: str, branch_id: str | None = None) -> float:
    q = db.query(func.coalesce(func.sum(StockMove.qty_change), 0)).filter(StockMove.ingredient_id == ingredient_id)
    if branch_id:
        q = q.filter(StockMove.branch_id == branch_id)
    total = q.scalar()
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


# ---------- CSV import/export helpers ----------

def _parse_csv_rows(csv_text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(StringIO(csv_text.strip()))
    return [dict({k.strip(): (v or "").strip() for k, v in row.items()}) for row in reader]


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
    branch_id: str | None = None,
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

    # effective branch for stock calculation
    eff_branch = ctx.branch_id or (branch_id or "").strip()

    out = []
    for ing in q.order_by(Ingredient.name.asc()).all():
        out.append({
            "id": ing.id,
            "tenant_id": ing.tenant_id,
            "name": ing.name,
            "uom": ing.uom,
            "min_level": _to_float(getattr(ing, "min_level", 0)),
            "qty_on_hand": _qty_on_hand(db, ing.id, eff_branch),
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
      "branch_id": "...",
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
    branch_id = body.get("branch_id")
    # if ctx.branch_id is set, enforce it
    if ctx.branch_id and branch_id and branch_id != ctx.branch_id:
         raise HTTPException(400, detail="branch_id mismatch")
    if ctx.branch_id:
         branch_id = ctx.branch_id

    p = Purchase(
        tenant_id=ctx.tenant_id,  # force to caller's tenant
        branch_id=branch_id,
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
                branch_id=branch_id,
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
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Returns ingredients where current qty_on_hand <= min_level.
    Used by dashboard / alert UI.
    """
    # Sum quantities only for this tenant's ingredients
    eff_branch = ctx.branch_id or (branch_id or "").strip()

    q = db.query(
            StockMove.ingredient_id,
            func.coalesce(func.sum(StockMove.qty_change), 0),
        ).join(Ingredient, Ingredient.id == StockMove.ingredient_id).filter(Ingredient.tenant_id == ctx.tenant_id)
    
    if eff_branch:
        q = q.filter(StockMove.branch_id == eff_branch)

    sums = q.group_by(StockMove.ingredient_id).all()
    levels = {ing_id: float(qty) for ing_id, qty in sums}

    res = []
    for ing in db.query(Ingredient).filter(Ingredient.tenant_id == ctx.tenant_id).all():
        qty = levels.get(ing.id, 0.0)
        min_level = _to_float(getattr(ing, "min_level", 0))  # Safe access
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


# ---------- CSV IMPORT / EXPORT ----------

@router.post("/ingredients/import_csv")
def import_ingredients_csv(
    csv_text: str,
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Import Ingredients from CSV.
    Expected headers: name,uom,min_level
    branch_id is taken from context or query param; tenant is always ctx.tenant_id.
    """
    rows = _parse_csv_rows(csv_text)
    created = 0
    updated = 0
    eff_branch = ctx.branch_id or (branch_id or "").strip()

    for row in rows:
        name = row.get("name") or ""
        uom = row.get("uom") or ""
        if not name or not uom:
            continue
        min_level = _to_float(row.get("min_level"))
        existing = (
            db.query(Ingredient)
            .filter(Ingredient.tenant_id == ctx.tenant_id, Ingredient.name == name)
            .first()
        )
        if existing:
            existing.uom = uom
            if hasattr(existing, "min_level"):
                existing.min_level = min_level
            updated += 1
        else:
            db.add(
                Ingredient(
                    tenant_id=ctx.tenant_id,
                    name=name,
                    uom=uom,
                    min_level=min_level,
                    image_url=None,
                )
            )
            created += 1

    db.commit()
    return {"created": created, "updated": updated}


@router.get("/ingredients/export_csv", response_class=PlainTextResponse)
def export_ingredients_csv(
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Export ingredients for the tenant as CSV.
    """
    rows = db.query(Ingredient).filter(Ingredient.tenant_id == ctx.tenant_id).order_by(Ingredient.name.asc()).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["name", "uom", "min_level"])
    for ing in rows:
        writer.writerow([ing.name, ing.uom, _to_float(getattr(ing, "min_level", 0))])
    return PlainTextResponse(output.getvalue(), media_type="text/csv")


@router.post("/cash/import_csv")
def import_cash_movements_csv(
    csv_text: str,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_perm("SETTINGS_EDIT")),
):
    """
    Import CashMovement rows from CSV.
    Expected headers: shift_id,kind,amount,reason
    shift_id must exist and belong to caller's branch/tenant.
    """
    rows = _parse_csv_rows(csv_text)
    created = 0

    for row in rows:
        shift_id = (row.get("shift_id") or "").strip()
        kind = (row.get("kind") or "").strip()
        amount = _to_float(row.get("amount"))
        reason = row.get("reason") or None

        if not shift_id or not kind or amount == 0:
            continue

        shift = db.get(Shift, shift_id)
        if not shift or (ctx.branch_id and shift.branch_id != ctx.branch_id):
            continue

        db.add(
            CashMovement(
                shift_id=shift_id,
                kind=kind,
                amount=amount,
                reason=reason,
            )
        )
        created += 1

    db.commit()
    return {"created": created}


@router.get("/cash/export_csv", response_class=PlainTextResponse)
def export_cash_movements_csv(
    shift_id: str | None = None,
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    ctx: AuthCtx = Depends(require_auth),
):
    """
    Export cash movements as CSV for a shift or branch.
    """
    q = db.query(CashMovement, Shift.branch_id).join(Shift, CashMovement.shift_id == Shift.id)
    eff_branch = ctx.branch_id or (branch_id or "").strip()
    if eff_branch:
        q = q.filter(Shift.branch_id == eff_branch)
    if shift_id:
        q = q.filter(CashMovement.shift_id == shift_id)

    rows = q.order_by(CashMovement.created_at.desc()).all()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["shift_id", "branch_id", "kind", "amount", "reason", "created_at"])
    for cm, br_id in rows:
        writer.writerow([cm.shift_id, br_id, cm.kind, _to_float(cm.amount), cm.reason or "", cm.created_at])

    return PlainTextResponse(output.getvalue(), media_type="text/csv")
