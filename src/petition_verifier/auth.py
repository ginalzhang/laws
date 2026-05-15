"""JWT auth helpers."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-use-32-char-random-string")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 168  # 7 days


bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def get_owner_email() -> str:
    return normalize_email(os.getenv("PVFY_OWNER_EMAIL"))


def is_owner_email(email: str | None) -> bool:
    owner_email = get_owner_email()
    return bool(owner_email and normalize_email(email) == owner_email)


def is_private_owner_record(user: object | None) -> bool:
    return bool(user and is_owner_email(getattr(user, "email", "")))


def is_private_owner_actor(user: dict) -> bool:
    if is_owner_email(user.get("email")):
        return True
    owner_email = get_owner_email()
    if not owner_email:
        return False
    try:
        user_id = int(user["user_id"])
    except (KeyError, TypeError, ValueError):
        return False
    from .storage import db

    return is_private_owner_record(db.get_user_by_id(user_id))


def filter_private_owner_records(users: list) -> list:
    return [user for user in users if not is_private_owner_record(user)]


def require_private_owner_for_target(target_user: object | None, actor: dict) -> None:
    if is_private_owner_record(target_user) and not is_private_owner_actor(actor):
        raise HTTPException(status_code=404, detail="Worker not found")


def create_token(user_id: int, role: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),  # noqa: UP045
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    return {"user_id": int(payload["sub"]), "role": payload["role"]}


def require_role(*roles: str):
    def dep(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),  # noqa: UP045
    ) -> dict:
        if not credentials:
            raise HTTPException(status_code=401, detail="Not authenticated")
        payload = decode_token(credentials.credentials)
        user = {"user_id": int(payload["sub"]), "role": payload["role"]}
        if roles and user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dep


require_worker  = require_role("boss", "admin", "worker", "field_manager", "petitioner", "office_worker")
require_manager = require_role("boss", "admin", "field_manager")  # can manage workers/shifts/schedule
require_admin   = require_role("boss", "admin")
require_boss    = require_role("boss")
