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
    command.upgrade(Config("alembic.ini"), "head")

    import petition_verifier.storage as storage  # noqa: PLC0415

    storage.db.reset()
    sys.modules.pop("petition_verifier.api", None)
    from petition_verifier.api import app as fastapi_app  # noqa: PLC0415

    return fastapi_app


@pytest.fixture
async def client(app):
    await app.router.startup()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await app.router.shutdown()


async def test_permanent_user_can_login_and_read_profile(client):
    login = await client.post(
        "/auth/login",
        json={"email": "arianafan2000@app.local", "password": "arianafan2000"},
    )

    assert login.status_code == 200
    token = login.json()["access_token"]

    profile = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert profile.status_code == 200
    assert profile.json() == {
        "user_id": login.json()["user_id"],
        "role": "boss",
        "full_name": "Gina",
        "email": "arianafan2000@app.local",
        "phone": "",
        "hourly_wage": 25.0,
    }


async def test_login_rejects_bad_password(client):
    response = await client.post(
        "/auth/login",
        json={"email": "arianafan2000@app.local", "password": "wrong-password"},
    )

    assert response.status_code == 401


async def test_dev_token_is_disabled_by_default(client):
    response = await client.get("/auth/dev-token")

    assert response.status_code == 403


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
