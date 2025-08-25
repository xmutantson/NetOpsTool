# netops/auth.py
from __future__ import annotations
import jwt
from datetime import datetime, timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import request, abort
from typing import Optional, Tuple
from .config import config

_ph = PasswordHasher()

def hash_password(pw: str) -> str:
    return _ph.hash(pw)

def verify_password(phash: str, pw: str) -> bool:
    try:
        return _ph.verify(phash, pw)
    except VerifyMismatchError:
        return False

def issue_token(station_id: int, token_salt: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": station_id,
        "iat": int(now.timestamp()),
        "exp": int((now + config.TOKEN_TTL).timestamp()),
        "salt": token_salt,
    }
    return jwt.encode(payload, config.NETOPS_JWT_SECRET, algorithm="HS256")

def _bearer_token() -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return auth.split(" ", 1)[1].strip()

def require_bearer() -> Tuple[int, dict]:
    token = _bearer_token()
    if not token:
        abort(401, description="Missing bearer token")
    try:
        payload = jwt.decode(token, config.NETOPS_JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        abort(401, description="Invalid token")
    return int(payload["sub"]), payload
