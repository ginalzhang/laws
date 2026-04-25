"""Auth routes: login, logout, me."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import (
    hash_password, verify_password, create_token, get_current_user
)
from ..storage import Database

router = APIRouter()
db = Database()


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
async def login(payload: LoginRequest):
    user = db.get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account is deactivated")
    token = create_token(user.id, user.role)
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "user_id": user.id,
        "full_name": user.full_name,
    }


@router.get("/dev-token")
async def dev_token():
    """Return a boss token without credentials. Only works when DEV_AUTO_LOGIN=true in env."""
    if os.getenv("DEV_AUTO_LOGIN", "").lower() != "true":
        raise HTTPException(403, "Dev auto-login is not enabled")
    users = db.list_users()
    boss = next((u for u in users if u.role == "boss"), None)
    if not boss:
        boss = next((u for u in users if u.role == "admin"), None)
    if not boss:
        raise HTTPException(404, "No boss/admin user found — run /seed-demo-data first")
    token = create_token(boss.id, boss.role)
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": boss.role,
        "user_id": boss.id,
        "full_name": boss.full_name,
    }


@router.post("/logout")
async def logout():
    return {"ok": True, "message": "Token invalidated client-side"}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    db_user = db.get_user_by_id(user["user_id"])
    if not db_user:
        raise HTTPException(404, "User not found")
    return {
        "user_id": db_user.id,
        "role": db_user.role,
        "full_name": db_user.full_name,
        "email": db_user.email,
        "phone": db_user.phone,
        "hourly_wage": db_user.hourly_wage,
    }
