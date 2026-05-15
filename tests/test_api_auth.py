from __future__ import annotations

import sys

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from alembic import command


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "api-auth.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
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


@pytest.fixture
async def client(app):
    await app.router.startup()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await app.router.shutdown()


def create_test_user(
    email: str,
    role: str,
    full_name: str = "Test User",
    password: str = "secret123",
) -> int:
    from petition_verifier.auth import hash_password
    from petition_verifier.storage import Database

    user = Database().create_user(email, hash_password(password), role, full_name)
    return user.id


def auth_headers(user_id: int, role: str) -> dict[str, str]:
    from petition_verifier.auth import create_token

    return {"Authorization": f"Bearer {create_token(user_id, role)}"}


def save_sample_project(project_id: str = "api-auth-project") -> None:
    from petition_verifier.models import (
        ExtractedSignature,
        NormalizedSignature,
        ProjectResult,
        VerificationResult,
        VerificationStatus,
    )
    from petition_verifier.storage import Database

    extracted = ExtractedSignature(
        line_number=1,
        page=1,
        raw_name="Ada Lovelace",
        raw_address="1 Main St",
        signature_present=True,
    )
    normalized = NormalizedSignature(
        line_number=1,
        page=1,
        first_name="Ada",
        last_name="Lovelace",
        street="1 Main St",
        signature_present=True,
    )
    result = ProjectResult(
        project_id=project_id,
        pdf_path="sample.pdf",
        total_lines=1,
        review=1,
        signatures=[
            VerificationResult(
                line_number=1,
                page=1,
                extracted=extracted,
                normalized=normalized,
                status=VerificationStatus.REVIEW,
            )
        ],
    )
    Database().save_project(result, county="Test County", cause="Test Cause")


async def test_project_read_routes_require_admin_and_block_workers(client):
    save_sample_project()
    worker_id = create_test_user("worker@example.com", "worker", "Worker")
    worker_headers = auth_headers(worker_id, "worker")
    admin_id = create_test_user("admin@example.com", "admin", "Admin")
    headers = auth_headers(admin_id, "admin")
    unsupported_role_headers = auth_headers(worker_id, "contractor")

    assert (await client.get("/projects")).status_code == 401
    assert (await client.get("/projects/api-auth-project/signatures")).status_code == 401
    assert (await client.get("/projects/api-auth-project/signatures/1")).status_code == 401
    assert (await client.get("/projects/api-auth-project/export")).status_code == 401
    assert (await client.get("/projects", headers=unsupported_role_headers)).status_code == 403
    assert (await client.get("/projects", headers=worker_headers)).status_code == 403
    assert (
        await client.get("/projects/api-auth-project/signatures", headers=worker_headers)
    ).status_code == 403
    assert (
        await client.get("/projects/api-auth-project/signatures/1", headers=worker_headers)
    ).status_code == 403
    assert (
        await client.get("/projects/api-auth-project/export", headers=worker_headers)
    ).status_code == 403

    projects = await client.get("/projects", headers=headers)
    signatures = await client.get("/projects/api-auth-project/signatures", headers=headers)
    detail = await client.get("/projects/api-auth-project/signatures/1", headers=headers)
    export = await client.get("/projects/api-auth-project/export", headers=headers)

    assert projects.status_code == 200
    assert signatures.status_code == 200
    assert signatures.json()["total"] == 1
    assert detail.status_code == 200
    assert detail.json()["raw_name"] == "Ada Lovelace"
    assert export.status_code == 200
    assert "text/csv" in export.headers["content-type"]


async def test_review_route_requires_admin_and_updates_when_authorized(client):
    save_sample_project()
    worker_id = create_test_user("worker@example.com", "worker", "Worker")
    admin_id = create_test_user("admin@example.com", "admin", "Admin")
    payload = {"override": "approved", "voter_id": "VOTER-1", "notes": "checked"}

    unauth = await client.post("/projects/api-auth-project/signatures/1/review", json=payload)
    worker = await client.post(
        "/projects/api-auth-project/signatures/1/review",
        json=payload,
        headers=auth_headers(worker_id, "worker"),
    )
    admin = await client.post(
        "/projects/api-auth-project/signatures/1/review",
        json=payload,
        headers=auth_headers(admin_id, "admin"),
    )

    assert unauth.status_code == 401
    assert worker.status_code == 403
    assert admin.status_code == 200
    assert admin.json() == {"ok": True}

    detail = await client.get(
        "/projects/api-auth-project/signatures/1",
        headers=auth_headers(admin_id, "admin"),
    )
    assert detail.json()["status"] == "approved"
    assert detail.json()["staff_notes"] == "checked"


async def test_processing_routes_require_admin_before_work_starts(client):
    save_sample_project()
    worker_id = create_test_user("worker@example.com", "worker", "Worker")
    headers = auth_headers(worker_id, "worker")

    process_data = {"voter_roll": "/definitely/missing.csv"}
    process_files = {"petition": ("petition.pdf", b"%PDF-1.4", "application/pdf")}
    old_process_files = {"pdf": ("petition.pdf", b"%PDF-1.4", "application/pdf")}
    fraud_files = {"petition": ("petition.pdf", b"%PDF-1.4", "application/pdf")}

    assert (
        await client.post("/process", data=process_data, files=process_files)
    ).status_code == 401
    assert (
        await client.post(
            "/process",
            data=process_data,
            files={"petition": ("petition.pdf", b"%PDF-1.4", "application/pdf")},
            headers=headers,
        )
    ).status_code == 403
    assert (
        await client.post(
            "/projects/api-auth-project/process",
            data=process_data,
            files=old_process_files,
        )
    ).status_code == 401
    assert (
        await client.post(
            "/projects/api-auth-project/process",
            data=process_data,
            files={"pdf": ("petition.pdf", b"%PDF-1.4", "application/pdf")},
            headers=headers,
        )
    ).status_code == 403
    assert (await client.post("/fraud-scan", files=fraud_files)).status_code == 401
    assert (
        await client.post(
            "/fraud-scan",
            files={"petition": ("petition.pdf", b"%PDF-1.4", "application/pdf")},
            headers=headers,
        )
    ).status_code == 403


async def test_assign_route_requires_admin_and_hides_private_owner_target(client, monkeypatch):
    from petition_verifier.storage import Database

    save_sample_project()
    monkeypatch.setenv("PVFY_OWNER_EMAIL", "private-owner@example.com")
    owner_id = create_test_user("private-owner@example.com", "boss", "Private Owner")
    admin_id = create_test_user("admin@example.com", "admin", "Admin")
    worker_id = create_test_user("worker@example.com", "worker", "Worker")

    unauth = await client.post(
        "/projects/api-auth-project/assign",
        json={"worker_id": worker_id},
    )
    worker_actor = await client.post(
        "/projects/api-auth-project/assign",
        json={"worker_id": worker_id},
        headers=auth_headers(worker_id, "worker"),
    )
    hidden_owner = await client.post(
        "/projects/api-auth-project/assign",
        json={"worker_id": owner_id},
        headers=auth_headers(admin_id, "admin"),
    )
    owner_assigns_self = await client.post(
        "/projects/api-auth-project/assign",
        json={"worker_id": owner_id},
        headers=auth_headers(owner_id, "boss"),
    )
    admin_assigns_worker = await client.post(
        "/projects/api-auth-project/assign",
        json={"worker_id": worker_id},
        headers=auth_headers(admin_id, "admin"),
    )

    assert unauth.status_code == 401
    assert worker_actor.status_code == 403
    assert hidden_owner.status_code == 404
    assert owner_assigns_self.status_code == 200
    assert owner_assigns_self.json()["worker_id"] == owner_id
    assert admin_assigns_worker.status_code == 200

    assignment = Database().get_project_worker("api-auth-project")
    assert assignment.worker_id == worker_id
    assert assignment.assigned_by == admin_id
