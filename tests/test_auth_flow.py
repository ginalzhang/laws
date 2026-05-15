from __future__ import annotations

import sys

import pytest
from alembic.config import Config
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from alembic import command


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "auth.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.delenv("DEV_AUTO_LOGIN", raising=False)
    monkeypatch.delenv("PVFY_BOOTSTRAP_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("PVFY_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("PVFY_BOOTSTRAP_ADMIN_ROLE", raising=False)
    monkeypatch.delenv("PVFY_BOOTSTRAP_ADMIN_NAME", raising=False)
    monkeypatch.delenv("PVFY_OWNER_EMAIL", raising=False)
    monkeypatch.delenv("PVFY_OWNER_PASSWORD", raising=False)
    monkeypatch.delenv("PVFY_OWNER_NAME", raising=False)
    command.upgrade(Config("alembic.ini"), "head")

    import petition_verifier.storage as storage  # noqa: PLC0415

    storage.db.reset()
    sys.modules.pop("petition_verifier.api", None)
    from petition_verifier.api import app as fastapi_app  # noqa: PLC0415

    return fastapi_app


def create_test_user(
    email: str = "boss@example.com",
    password: str = "secret123",
    role: str = "boss",
    full_name: str = "Boss User",
) -> int:
    from petition_verifier.auth import hash_password
    from petition_verifier.storage import Database

    user = Database().create_user(email, hash_password(password), role, full_name)
    return user.id


def auth_headers(user_id: int, role: str) -> dict[str, str]:
    from petition_verifier.auth import create_token

    return {"Authorization": f"Bearer {create_token(user_id, role)}"}


@pytest.fixture
async def client(app):
    await app.router.startup()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await app.router.shutdown()


async def test_created_user_can_login_and_read_profile(client):
    from petition_verifier.auth import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME

    user_id = create_test_user()

    login = await client.post(
        "/auth/login",
        json={"email": "boss@example.com", "password": "secret123"},
    )

    assert login.status_code == 200
    token = login.json()["access_token"]
    set_cookie_headers = login.headers.get_list("set-cookie")
    assert login.cookies.get(ACCESS_COOKIE_NAME)
    assert login.cookies.get(REFRESH_COOKIE_NAME)
    assert any(
        header.startswith(f"{ACCESS_COOKIE_NAME}=")
        and "httponly" in header.lower()
        and "max-age=900" in header.lower()
        for header in set_cookie_headers
    )
    assert any(
        header.startswith(f"{REFRESH_COOKIE_NAME}=")
        and "httponly" in header.lower()
        and "max-age=2592000" in header.lower()
        for header in set_cookie_headers
    )

    profile = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert profile.status_code == 200
    assert profile.json() == {
        "user_id": user_id,
        "role": "boss",
        "full_name": "Boss User",
        "email": "boss@example.com",
        "phone": "",
        "hourly_wage": 25.0,
    }

    cookie_profile = await client.get("/auth/me")

    assert cookie_profile.status_code == 200
    assert cookie_profile.json() == profile.json()


async def test_refresh_issues_new_access_cookie(client):
    from petition_verifier.auth import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME

    create_test_user()
    login = await client.post(
        "/auth/login",
        json={"email": "boss@example.com", "password": "secret123"},
    )
    old_access_token = login.cookies.get(ACCESS_COOKIE_NAME)
    refresh_token = login.cookies.get(REFRESH_COOKIE_NAME)
    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE_NAME, refresh_token)

    refresh = await client.post("/auth/refresh")

    assert refresh.status_code == 200
    assert refresh.cookies.get(ACCESS_COOKIE_NAME)
    assert refresh.cookies.get(ACCESS_COOKIE_NAME) != old_access_token
    assert refresh.cookies.get(REFRESH_COOKIE_NAME) is None
    assert "access_token" in refresh.json()

    profile = await client.get("/auth/me")

    assert profile.status_code == 200
    assert profile.json()["email"] == "boss@example.com"


async def test_logout_clears_auth_cookies(client):
    from petition_verifier.auth import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME

    create_test_user()
    login = await client.post(
        "/auth/login",
        json={"email": "boss@example.com", "password": "secret123"},
    )
    assert login.cookies.get(ACCESS_COOKIE_NAME)
    assert login.cookies.get(REFRESH_COOKIE_NAME)

    logout = await client.post("/auth/logout")

    assert logout.status_code == 200
    set_cookie_headers = logout.headers.get_list("set-cookie")
    assert any(
        header.startswith(f"{ACCESS_COOKIE_NAME}=")
        and "max-age=0" in header.lower()
        for header in set_cookie_headers
    )
    assert any(
        header.startswith(f"{REFRESH_COOKIE_NAME}=")
        and "max-age=0" in header.lower()
        for header in set_cookie_headers
    )

    profile = await client.get("/auth/me")

    assert profile.status_code == 401


async def test_refresh_token_is_not_accepted_as_access(client):
    from petition_verifier.auth import ACCESS_COOKIE_NAME, create_refresh_token

    user_id = create_test_user()
    refresh_token = create_refresh_token(user_id, "boss")

    bearer_response = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    client.cookies.set(ACCESS_COOKIE_NAME, refresh_token)
    cookie_response = await client.get("/auth/me")

    assert bearer_response.status_code == 401
    assert cookie_response.status_code == 401


async def test_login_rejects_bad_password(client):
    create_test_user()

    response = await client.post(
        "/auth/login",
        json={"email": "boss@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401


async def test_dev_token_is_disabled_by_default(client):
    response = await client.get("/auth/dev-token")

    assert response.status_code == 403


async def test_startup_does_not_create_legacy_accounts_by_default(app):
    from petition_verifier.storage import Database

    await app.router.startup()
    try:
        assert Database().list_users() == []
    finally:
        await app.router.shutdown()


async def test_startup_recreates_owner_account_when_owner_password_is_set(tmp_path, monkeypatch):
    db_path = tmp_path / "owner.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PVFY_OWNER_EMAIL", "private-owner@example.com")
    monkeypatch.setenv("PVFY_OWNER_PASSWORD", "private-owner-password")
    monkeypatch.setenv("PVFY_OWNER_NAME", "Private Owner")
    command.upgrade(Config("alembic.ini"), "head")

    import petition_verifier.storage as storage  # noqa: PLC0415
    from petition_verifier.auth import verify_password  # noqa: PLC0415
    from petition_verifier.storage import Database  # noqa: PLC0415

    storage.db.reset()
    sys.modules.pop("petition_verifier.api", None)
    from petition_verifier.api import app as fastapi_app  # noqa: PLC0415

    await fastapi_app.router.startup()
    try:
        user = Database().get_user_by_email("private-owner@example.com")
        assert user is not None
        assert user.role == "boss"
        assert user.full_name == "Private Owner"
        assert verify_password("private-owner-password", user.password_hash)
    finally:
        await fastapi_app.router.shutdown()


async def test_workers_list_hides_private_owner(client, monkeypatch):
    monkeypatch.setenv("PVFY_OWNER_EMAIL", "private-owner@example.com")
    create_test_user(
        email="private-owner@example.com",
        role="boss",
        full_name="Private Owner",
    )
    admin_id = create_test_user(
        email="admin@example.com",
        role="admin",
        full_name="Admin User",
    )
    create_test_user(
        email="worker@example.com",
        role="worker",
        full_name="Worker User",
    )

    response = await client.get("/workers", headers=auth_headers(admin_id, "admin"))

    assert response.status_code == 200
    emails = {worker["email"] for worker in response.json()}
    assert "private-owner@example.com" not in emails
    assert {"admin@example.com", "worker@example.com"} <= emails


async def test_non_owner_cannot_read_or_update_private_owner(client, monkeypatch):
    monkeypatch.setenv("PVFY_OWNER_EMAIL", "private-owner@example.com")
    owner_id = create_test_user(
        email="private-owner@example.com",
        role="boss",
        full_name="Private Owner",
    )
    boss_id = create_test_user(
        email="boss@example.com",
        role="boss",
        full_name="Non Owner Boss",
    )

    headers = auth_headers(boss_id, "boss")
    read_response = await client.get(f"/workers/{owner_id}", headers=headers)
    update_response = await client.patch(
        f"/workers/{owner_id}",
        json={"full_name": "Changed Owner"},
        headers=headers,
    )

    assert read_response.status_code == 404
    assert update_response.status_code == 404

    from petition_verifier.storage import Database

    assert Database().get_user_by_id(owner_id).full_name == "Private Owner"


async def test_worker_create_and_update_cannot_claim_private_owner_email(client, monkeypatch):
    monkeypatch.setenv("PVFY_OWNER_EMAIL", "private-owner@example.com")
    boss_id = create_test_user(email="boss@example.com", role="boss", full_name="Boss")
    worker_id = create_test_user(email="worker@example.com", role="worker", full_name="Worker")
    headers = auth_headers(boss_id, "boss")

    create_response = await client.post(
        "/workers",
        json={
            "email": "private-owner@example.com",
            "password": "secret123",
            "role": "worker",
            "full_name": "Fake Owner",
        },
        headers=headers,
    )
    update_response = await client.patch(
        f"/workers/{worker_id}",
        json={"email": "private-owner@example.com"},
        headers=headers,
    )

    assert create_response.status_code == 403
    assert update_response.status_code == 403


async def test_field_manager_cannot_create_privileged_roles(client):
    manager_id = create_test_user(
        email="manager@example.com",
        role="field_manager",
        full_name="Manager",
    )

    response = await client.post(
        "/workers",
        json={
            "email": "new-boss@example.com",
            "password": "secret123",
            "role": "boss",
            "full_name": "New Boss",
        },
        headers=auth_headers(manager_id, "field_manager"),
    )

    assert response.status_code == 403


async def test_leaderboard_hides_private_owner(client, monkeypatch):
    monkeypatch.setenv("PVFY_OWNER_EMAIL", "private-owner@example.com")
    owner_id = create_test_user(
        email="private-owner@example.com",
        role="boss",
        full_name="Private Owner",
    )
    worker_id = create_test_user(
        email="worker@example.com",
        role="worker",
        full_name="Worker User",
    )

    response = await client.get("/leaderboard", headers=auth_headers(worker_id, "worker"))

    assert response.status_code == 200
    worker_ids = {entry["worker_id"] for entry in response.json()["leaderboard"]}
    assert owner_id not in worker_ids
    assert worker_id in worker_ids


async def test_startup_bootstraps_admin_from_explicit_env(tmp_path, monkeypatch):
    db_path = tmp_path / "bootstrap.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PVFY_BOOTSTRAP_ADMIN_EMAIL", "first-admin@example.com")
    monkeypatch.setenv("PVFY_BOOTSTRAP_ADMIN_PASSWORD", "secret123")
    monkeypatch.setenv("PVFY_BOOTSTRAP_ADMIN_NAME", "First Admin")
    monkeypatch.setenv("PVFY_BOOTSTRAP_ADMIN_ROLE", "admin")
    command.upgrade(Config("alembic.ini"), "head")

    import petition_verifier.storage as storage  # noqa: PLC0415
    from petition_verifier.auth import verify_password  # noqa: PLC0415
    from petition_verifier.storage import Database  # noqa: PLC0415

    storage.db.reset()
    sys.modules.pop("petition_verifier.api", None)
    from petition_verifier.api import app as fastapi_app  # noqa: PLC0415

    await fastapi_app.router.startup()
    try:
        user = Database().get_user_by_email("first-admin@example.com")
        assert user is not None
        assert user.role == "admin"
        assert user.full_name == "First Admin"
        assert verify_password("secret123", user.password_hash)
    finally:
        await fastapi_app.router.shutdown()


async def test_require_boss_rejects_non_boss_token():
    from petition_verifier.auth import create_token, require_boss

    gate = FastAPI()

    @gate.get("/boss-only")
    async def boss_only(user: dict = Depends(require_boss)):
        return user

    transport = ASGITransport(app=gate)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        worker = create_token(1, "worker")
        worker_response = await ac.get(
            "/boss-only",
            headers={"Authorization": f"Bearer {worker}"},
        )

        boss = create_token(2, "boss")
        boss_response = await ac.get(
            "/boss-only",
            headers={"Authorization": f"Bearer {boss}"},
        )

    assert worker_response.status_code == 403
    assert boss_response.status_code == 200
    assert boss_response.json() == {"user_id": 2, "role": "boss"}
