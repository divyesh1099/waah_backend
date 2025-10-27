# app/scoping.py
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.core import Branch, MenuCategory, MenuItem

def scope_query_tenant(q, Model, tenant_id: str):
    if hasattr(Model, "tenant_id"):
        q = q.filter(getattr(Model, "tenant_id") == tenant_id)
    return q

def scope_query_branch(q, Model, branch_id: str | None):
    if branch_id and hasattr(Model, "branch_id"):
        q = q.filter(getattr(Model, "branch_id") == branch_id)
    return q

def assert_same_tenant(obj, tenant_id: str):
    if hasattr(obj, "tenant_id") and obj.tenant_id != tenant_id:
        # 404 to avoid leaking what exists
        raise HTTPException(404, "not found")

def assert_same_branch(obj, branch_id: str | None):
    if branch_id and hasattr(obj, "branch_id") and obj.branch_id != branch_id:
        raise HTTPException(404, "not found")

def branch_of_table_belongs_to_tenant(db: Session, branch_id: str, tenant_id: str):
    br = db.get(Branch, branch_id)
    if not br or br.tenant_id != tenant_id:
        raise HTTPException(404, "not found")

def ensure_item_in_branch(db: Session, item: MenuItem, branch_id: str | None):
    """MenuItem has no branch_id; verify through its category."""
    if not branch_id:
        return
    cat = db.get(MenuCategory, item.category_id)
    if not cat or cat.branch_id != branch_id:
        raise HTTPException(404, "not found")
