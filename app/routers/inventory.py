from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from app.db import get_db
from app.deps import require_auth, has_perm, require_perm
from app.models.core import Ingredient, Shift, CashMovement, StockMove
from app.util.audit import log_audit

router = APIRouter(prefix="/inventory", tags=["inventory"])

@router.get("/stock_report")
def stock_report(db: Session = Depends(get_db), sub: str = Depends(require_auth)):
    # Opening (sum before today 00:00), Used (negative sales today), Closing (cumulative)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    # aggregate all-time
    agg = db.query(StockMove.ingredient_id, func.coalesce(func.sum(StockMove.qty_change),0)).group_by(StockMove.ingredient_id).all()
    closing = {k: float(v) for k,v in agg}
    # very simple used = total negative changes (SALE/WASTAGE/ADJUST negative)
    used = {}
    for ing_id, qty in closing.items():
        used[ing_id] = 0.0
    rows = db.query(StockMove.ingredient_id, StockMove.qty_change).all()
    for ing, delta in rows:
        if float(delta) < 0:
            used[ing] += float(delta)
    res = []
    for ing in db.query(Ingredient).all():
        c = closing.get(ing.id, 0.0)
        u = used.get(ing.id, 0.0)
        o = c - u  # crude opening
        res.append({"ingredient_id": ing.id, "name": ing.name, "opening": o, "used": u, "closing": c})
    return res
