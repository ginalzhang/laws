"""Auth routes: login, logout, me."""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import (
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_csrf_token,
    create_refresh_token_value,
    create_token,
    dev_auto_login_enabled,
    get_current_user,
    hash_password,
    hash_refresh_token,
    validate_csrf_request,
    verify_password,
)
from ..storage import db

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


def _cookie_secure(request: Request) -> bool:
    host = request.url.hostname or ""
    local_hosts = {"localhost", "127.0.0.1", "testserver"}
    return host not in local_hosts and not dev_auto_login_enabled()


def _set_cookie(
    response: Response,
    key: str,
    value: str,
    max_age: int,
    *,
    request: Request,
    http_only: bool = True,
) -> None:
    response.set_cookie(
        key,
        value,
        max_age=max_age,
        httponly=http_only,
        secure=_cookie_secure(request),
        samesite="lax",
        path="/",
    )


def _clear_session_cookies(response: Response, request: Request) -> None:
    secure = _cookie_secure(request)
    for name in ("pv_access", "pv_refresh", "pv_csrf"):
        response.delete_cookie(name, path="/", secure=secure, samesite="lax")


def _issue_session(response: Response, request: Request, user) -> str:
    access_token = create_token(user.id, user.role)
    refresh_token = create_refresh_token_value()
    expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    db.create_refresh_token(
        user_id=user.id,
        token_hash=hash_refresh_token(refresh_token),
        expires_at=expires_at,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=request.client.host if request.client else "",
    )
    csrf = create_csrf_token()
    _set_cookie(response, "pv_access", access_token, 15 * 60, request=request)
    _set_cookie(response, "pv_refresh", refresh_token, REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60, request=request)
    _set_cookie(response, "pv_csrf", csrf, REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60, request=request, http_only=False)
    return access_token


def _legacy_login_body(user, access_token: str) -> dict:
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "user_id": user.id,
        "full_name": user.full_name,
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    user = db.get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account is deactivated")
    token = _issue_session(response, request, user)
    return _legacy_login_body(user, token)


@router.get("/dev-token")
async def dev_token(request: Request, response: Response):
    """Return a boss token without credentials. Only works when DEV_AUTO_LOGIN=true in env."""
    if not dev_auto_login_enabled():
        raise HTTPException(403, "Dev auto-login is not enabled")
    users = db.list_users()
    boss = next((u for u in users if u.role == "boss"), None)
    if not boss:
        boss = next((u for u in users if u.role == "admin"), None)
    if not boss:
        raise HTTPException(404, "No boss/admin user found — run /seed-demo-data first")
    token = _issue_session(response, request, boss)
    return _legacy_login_body(boss, token)


class NameLoginRequest(BaseModel):
    full_name: str


@router.post("/login-by-name")
async def login_by_name(payload: NameLoginRequest, request: Request, response: Response):
    if not dev_auto_login_enabled():
        raise HTTPException(403, "Name login is only available in development")
    user = db.get_user_by_name(payload.full_name)
    if not user:
        raise HTTPException(401, "Name not found — check spelling or ask your manager")
    if not user.is_active:
        raise HTTPException(403, "Account is deactivated")
    token = _issue_session(response, request, user)
    return _legacy_login_body(user, token)


@router.get("/active-users")
async def list_active_users():
    """Return active user names for the development name-selector UI."""
    if not dev_auto_login_enabled():
        raise HTTPException(403, "Name selector is only available in development")
    role_labels = {
        "field_manager": "Field Manager",
        "evan":          "Field Manager",
        "worker":        "Worker",
        "petitioner":    "Petitioner",
        "office_worker": "Staff",
    }
    users = db.list_users()
    seen: set[str] = set()
    result = []
    for u in sorted(users, key=lambda u: (0 if u.is_active else 1, u.id)):
        if not u.is_active or u.role in ("boss", "admin", "field_manager", "evan", "evann"):
            continue
        key = u.full_name.strip().lower()
        if key not in seen:
            seen.add(key)
            result.append({"full_name": u.full_name, "role_label": role_labels.get(u.role, u.role.title())})
    result.sort(key=lambda x: x["full_name"].lower())
    return result


class FMPasswordRequest(BaseModel):
    password: str


@router.post("/fm-users")
async def fm_users(payload: FMPasswordRequest):
    """Verify the FM team password and return the list of field managers."""
    stored = db.get_setting("fm_password")
    if not stored:
        raise HTTPException(403, "Field-manager team login is not configured")
    if payload.password.strip() != stored.strip():
        raise HTTPException(401, "Wrong password")
    users = db.list_users()
    seen: set[str] = set()
    deduped = []
    for u in sorted(users, key=lambda u: (0 if u.is_active else 1, u.id)):
        if not u.is_active or u.role not in ("field_manager",):
            continue
        key = u.full_name.strip().lower()
        if key not in seen:
            seen.add(key)
            deduped.append({"full_name": u.full_name, "role_label": "Field Manager"})
    deduped.sort(key=lambda x: x["full_name"].lower())
    return deduped


class UpdateFMPasswordRequest(BaseModel):
    new_password: str


@router.put("/fm-password")
async def update_fm_password(payload: UpdateFMPasswordRequest, user: dict = Depends(get_current_user)):
    if user["role"] != "boss":
        raise HTTPException(403, "Boss only")
    if not payload.new_password.strip():
        raise HTTPException(400, "Password cannot be empty")
    db.set_setting("fm_password", payload.new_password.strip())
    return {"ok": True}


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


@router.post("/scan-login")
async def scan_login(payload: dict, request: Request, response: Response):
    if not dev_auto_login_enabled():
        raise HTTPException(403, "Scan login is only available in development")
    expected = os.getenv("SCAN_LOGIN_PASSWORD", "meow")
    if payload.get("password") != expected:
        raise HTTPException(401, "Wrong password")
    users = db.list_users()
    boss = next((u for u in users if u.role in ("boss", "admin")), None)
    if not boss:
        raise HTTPException(404, "No admin user found — run seed first")
    token = _issue_session(response, request, boss)
    return _legacy_login_body(boss, token)


@router.post("/logout")
async def logout(request: Request, response: Response):
    refresh_token = request.cookies.get("pv_refresh")
    if refresh_token:
        validate_csrf_request(request)
        db.revoke_refresh_token(hash_refresh_token(refresh_token))
    _clear_session_cookies(response, request)
    return {"ok": True, "message": "Session ended"}


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    refresh_token = request.cookies.get("pv_refresh")
    if not refresh_token:
        raise HTTPException(401, "Refresh token is missing")
    validate_csrf_request(request)
    token_hash = hash_refresh_token(refresh_token)
    row = db.get_refresh_token(token_hash)
    if not row or row.revoked_at or row.expires_at <= datetime.utcnow():
        raise HTTPException(401, "Refresh token is invalid or expired")
    user = db.get_user_by_id(row.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "User is inactive or missing")
    db.revoke_refresh_token(token_hash)
    token = _issue_session(response, request, user)
    return _legacy_login_body(user, token)


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
