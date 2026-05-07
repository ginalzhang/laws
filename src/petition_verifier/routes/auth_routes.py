"""Auth routes: login, logout, me."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import (
    hash_password, verify_password, create_token, get_current_user
)
from ..storage import db

router = APIRouter()


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


class NameLoginRequest(BaseModel):
    full_name: str


@router.post("/login-by-name")
async def login_by_name(payload: NameLoginRequest):
    user = db.get_user_by_name(payload.full_name)
    if not user:
        raise HTTPException(401, "Name not found — check spelling or ask your manager")
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


@router.get("/active-users")
async def list_active_users():
    """Public — returns active user names for the login name-selector UI."""
    role_labels = {
        "field_manager": "Field Manager",
        "evan":          "Field Manager",
        "worker":        "Worker",
        "petitioner":    "Petitioner",
        "office_worker": "Staff",
    }
    users = db.list_users()
    result = [
        {"full_name": u.full_name, "role_label": role_labels.get(u.role, u.role.title())}
        for u in users
        if u.is_active and u.role not in ("boss", "admin")
    ]
    result.sort(key=lambda x: x["full_name"].lower())
    return result


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.patch("/me/password")
async def change_password(payload: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    db_user = db.get_user_by_id(user["user_id"])
    if not db_user:
        raise HTTPException(404, "User not found")
    if not verify_password(payload.current_password, db_user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    if len(payload.new_password) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")
    db.update_user(user["user_id"], password_hash=hash_password(payload.new_password))
    return {"ok": True}


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
