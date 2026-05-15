from __future__ import annotations

import os
import sys

from httpx import ASGITransport, AsyncClient
import pytest
from alembic import command
from alembic.config import Config


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "review.db"
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id='root'></div>")

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("WEB_DIST_DIR", str(dist))
    command.upgrade(Config("alembic.ini"), "head")

    import petition_verifier.storage as storage  # noqa: PLC0415

    storage.db.reset()
    sys.modules.pop("petition_verifier.api", None)
    from petition_verifier.api import app as fastapi_app  # noqa: PLC0415

    return fastapi_app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_review_route_serves_react_build(client):
    response = await client.get("/review")

    assert response.status_code == 200
    assert "root" in response.text


async def test_review_deep_link_serves_react_build(client):
    response = await client.get("/review/projects/demo-project")

    assert response.status_code == 200
    assert "root" in response.text
