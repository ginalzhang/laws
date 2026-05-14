"""JWT auth helpers."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

_DEV_SECRET_KEY = "dev-only-change-me"
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 168  # 7 days


bearer = HTTPBearer(auto_error=False)


def dev_auto_login_enabled() -> bool:
    return os.getenv("DEV_AUTO_LOGIN", "").lower() == "true"


def _jwt_secret() -> str:
    if SECRET_KEY:
        return SECRET_KEY
    if dev_auto_login_enabled():
        return _DEV_SECRET_KEY
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="SECRET_KEY is not configured",
    )


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_token(user_id: int, role: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=401, detail="Invalid token subject") from e
    from .storage import db
    db_user = db.get_user_by_id(user_id)
    if not db_user or not db_user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or missing")
    return {"user_id": db_user.id, "role": db_user.role}


def require_role(*roles: str):
    def dep(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> dict:
        user = get_current_user(credentials)
        if roles and user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dep


require_worker   = require_role("boss", "admin", "worker", "field_manager", "evan", "evann", "petitioner", "office_worker")
require_manager  = require_role("boss", "admin", "field_manager", "evan", "evann")
require_admin    = require_role("boss", "admin")
require_boss     = require_role("boss")
require_payroll  = require_role("boss", "admin", "evann")  # payroll access: boss, admin, evann only
