import jwt
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from uuid import UUID
from typing import Union
from argon2 import PasswordHasher
from app.config import settings

ph = PasswordHasher()

def hash_pw(p: str) -> str:
    return ph.hash(p)

def verify_pw(hashv: str, p: str) -> bool:
    try:
        ph.verify(hashv, p)
        # Optional: refresh to stronger params over time
        if ph.check_needs_rehash(hashv):
            pass
        return True
    except Exception:
        return False

def _jsonify_claim_value(v):
    # Make common non-JSON types safe for JWT payloads.
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        # JWT wants numeric date (seconds since epoch, UTC)
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return int(v.timestamp())
    if isinstance(v, date):
        dt_utc = datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
        return int(dt_utc.timestamp())
    return v

def create_token(sub_or_claims: Union[str, dict], exp_min: int = None) -> str:
    """
    Backward-compatible JWT creator.

    - Legacy: create_token("user_id")
    - New:    create_token({"sub": user_id, "tenant_id": t_id, "branch_id": b_id, ...})

    Server-controlled standard claims (iss, iat, exp) are always enforced.
    """
    now = datetime.now(timezone.utc)
    exp_minutes = int(exp_min) if isinstance(exp_min, int) and exp_min > 0 else int(getattr(settings, "JWT_EXP_MIN", 60))
    exp = now + timedelta(minutes=exp_minutes)

    base = {
        "iss": getattr(settings, "JWT_ISS", "app"),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }

    if isinstance(sub_or_claims, str):
        payload = {"sub": str(sub_or_claims)}
    else:
        claims = dict(sub_or_claims or {})
        # Ensure sub present and stringified if available
        if "sub" in claims and claims["sub"] is not None:
            claims["sub"] = str(claims["sub"])
        elif "user_id" in claims and claims["user_id"] is not None:
            claims["sub"] = str(claims["user_id"])

        # Sanitize claim values (UUID, Decimal, datetime, etc.)
        claims = {k: _jsonify_claim_value(v) for k, v in claims.items()}

        # Do not allow caller to override standard server claims
        for k in ("iss", "iat", "exp"):
            claims.pop(k, None)

        payload = claims

    payload = {**payload, **base}
    secret = getattr(settings, "APP_SECRET")
    return jwt.encode(payload, secret, algorithm="HS256")
