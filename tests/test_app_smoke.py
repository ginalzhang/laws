from __future__ import annotations


def test_fastapi_app_imports_and_healthcheck_works(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app-smoke.db'}")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from fastapi.testclient import TestClient
    from petition_verifier.api import app

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
