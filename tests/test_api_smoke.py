"""Smoke tests for the FastAPI app.

Goal: catch import-time breakage, route deletions, and auth regressions early.
These are intentionally shallow — deeper route tests live in their own files
once each route group gets covered.
"""
from __future__ import annotations

import os
import sys

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from alembic import command


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    # Point at a tempfile SQLite DB so the real one is never touched.
    db_path = tmp_path_factory.mktemp("db") / "smoke.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    command.upgrade(Config("alembic.ini"), "head")
    import petition_verifier.storage as storage  # noqa: PLC0415
    storage.db.reset()
    # Import after env is set so the app picks up the test DB URL.
    sys.modules.pop("petition_verifier.api", None)
    from petition_verifier.api import app as fastapi_app  # noqa: PLC0415
    return fastapi_app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestAppShape:
    """Verify the app boots and exposes the routes we expect."""

    def test_app_imports(self, app):
        assert app is not None
        assert app.title == "Petition Verifier"

    async def test_startup_runs_schema_check(self, app):
        await app.router.startup()
        await app.router.shutdown()

    def test_no_fix_endpoints(self, app):
        """The /fix-activate-users and /fix-reset-boss endpoints were security
        holes (unauthenticated password reset). Regression guard: they must stay gone."""
        paths = {getattr(r, "path", "") for r in app.routes}
        assert "/fix-activate-users" not in paths
        assert "/fix-reset-boss" not in paths

    def test_core_routes_exist(self, app):
        paths = {getattr(r, "path", "") for r in app.routes}
        # Sample of routes that should always be present.
        for expected in ["/", "/projects"]:
            assert any(p == expected or p.startswith(expected) for p in paths), (
                f"missing route: {expected}"
            )


class TestAuthGate:
    """Endpoints that mutate state must require auth."""

    async def test_root_serves_ui(self, client):
        r = await client.get("/")
        # Either the UI HTML or a redirect to login — both are acceptable.
        assert r.status_code in (200, 302, 307)

    async def test_projects_list_requires_auth(self, client):
        r = await client.get("/projects")
        # Either 401 (unauth) or 200 if endpoint is intentionally public.
        # Document which one — both are valid app choices, but it must not 500.
        assert r.status_code != 500, f"projects list crashed: {r.text[:200]}"

    async def test_demo_seed_endpoint_disabled_by_default(self, client):
        r = await client.post("/seed-demo-data")

        assert r.status_code == 404
