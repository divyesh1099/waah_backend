import jwt
from datetime import datetime, timedelta, timezone
from argon2 import PasswordHasher
from app.config import settings

ph = PasswordHasher()

def hash_pw(p: str) -> str:
    return ph.hash(p)

def verify_pw(hashv: str, p: str) -> bool:
    try:
        ph.verify(hashv, p)
        return True
    except Exception:
        return False

def create_token(sub: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.JWT_EXP_MIN)
    payload = {"sub": sub, "iss": settings.JWT_ISS, "iat": int(now.timestamp()), "exp": int(exp.timestamp())}
    return jwt.encode(payload, settings.APP_SECRET, algorithm="HS256")

