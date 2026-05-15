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


@pytest.fixture
async def client(app):
    await app.router.startup()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await app.router.shutdown()


async def test_created_user_can_login_and_read_profile(client):
    user_id = create_test_user()

    login = await client.post(
        "/auth/login",
        json={"email": "boss@example.com", "password": "secret123"},
    )

    assert login.status_code == 200
    token = login.json()["access_token"]

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
