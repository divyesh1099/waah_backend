from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.schemas.common import Token
from app.util.security import create_token, verify_pw
from app.models.core import User
from app.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/login", response_model=Token)
def login(mobile: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.mobile == mobile).first()
    if not user or not verify_pw(user.pass_hash, password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return Token(access_token=create_token(user.id))