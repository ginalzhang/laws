"""Auth routes: login, logout, me."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import (
    ACCESS_COOKIE_NAME,
    ACCESS_TOKEN_MAX_AGE_SECONDS,
    REFRESH_COOKIE_NAME,
    REFRESH_JWT_TYPE,
    REFRESH_TOKEN_MAX_AGE_SECONDS,
    create_access_token,
    create_refresh_token,
    create_token,
    decode_token,
    filter_private_owner_records,
    get_current_user,
    hash_password,
    verify_password,
)
from ..storage import db

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


def _cookie_secure() -> bool:
    return os.getenv("AUTH_COOKIE_SECURE", "").lower() in {"1", "true", "yes"}


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str | None = None) -> None:
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access_token,
        max_age=ACCESS_TOKEN_MAX_AGE_SECONDS,
        path="/",
        secure=_cookie_secure(),
        httponly=True,
        samesite="lax",
    )
    if refresh_token is not None:
        response.set_cookie(
            REFRESH_COOKIE_NAME,
            refresh_token,
            max_age=REFRESH_TOKEN_MAX_AGE_SECONDS,
            path="/",
            secure=_cookie_secure(),
            httponly=True,
            samesite="lax",
        )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        ACCESS_COOKIE_NAME,
        path="/",
        secure=_cookie_secure(),
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path="/",
        secure=_cookie_secure(),
        httponly=True,
        samesite="lax",
    )


@router.post("/login")
async def login(payload: LoginRequest, response: Response):
    user = db.get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account is deactivated")
    token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id, user.role)
    _set_auth_cookies(response, token, refresh_token)
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "user_id": user.id,
        "full_name": user.full_name,
    }


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(401, "Not authenticated")

    payload = decode_token(refresh_token, expected_kind=REFRESH_JWT_TYPE)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(401, "Invalid or expired token") from e

    user = db.get_user_by_id(user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "Invalid or expired token")

    access_token = create_access_token(user.id, user.role)
    _set_auth_cookies(response, access_token)
    return {
        "access_token": access_token,
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
    users = filter_private_owner_records(db.list_users())
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
async def logout(response: Response):
    _clear_auth_cookies(response)
    return {"ok": True, "message": "Token cookies cleared"}


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
