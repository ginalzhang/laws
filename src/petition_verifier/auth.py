"""JWT auth helpers."""
from __future__ import annotations

import os
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

_DEV_SECRET_KEY = "dev-only-change-me"
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 30
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


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
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=ALGORITHM)


def create_refresh_token_value() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e


def _user_from_token(token: str) -> dict:
    payload = decode_token(token)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=401, detail="Invalid token subject") from e
    from .storage import db
    db_user = db.get_user_by_id(user_id)
    if not db_user or not db_user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or missing")
    return {"user_id": db_user.id, "role": db_user.role}


def validate_csrf_request(request: Request) -> None:
    if request.method not in UNSAFE_METHODS:
        return
    cookie_token = request.cookies.get("pv_csrf", "")
    header_token = request.headers.get("x-csrf-token", "")
    if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> dict:
    if credentials:
        return _user_from_token(credentials.credentials)
    cookie_token = request.cookies.get("pv_access")
    if not cookie_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    validate_csrf_request(request)
    return _user_from_token(cookie_token)


def require_role(*roles: str):
    def dep(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ) -> dict:
        user = get_current_user(request, credentials)
        if roles and user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dep


require_worker   = require_role("boss", "admin", "worker", "field_manager", "evan", "evann", "petitioner", "office_worker")
require_manager  = require_role("boss", "admin", "field_manager", "evan", "evann")
require_admin    = require_role("boss", "admin")
require_boss     = require_role("boss")
require_payroll  = require_role("boss", "admin", "evann")  # payroll access: boss, admin, evann only
