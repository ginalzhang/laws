from __future__ import annotations

from fastapi.testclient import TestClient

from tests.db_helpers import migrated_database


def _client_with_user(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    import petition_verifier.auth as auth
    import petition_verifier.storage as storage
    import petition_verifier.api as api_mod
    import petition_verifier.routes.auth_routes as auth_routes

    auth.SECRET_KEY = "test-secret-key"
    database = migrated_database(tmp_path / "auth.db")
    user = database.create_user(
        "admin@example.com",
        auth.hash_password("long-secure-password"),
        "boss",
        "Admin User",
    )
    monkeypatch.setattr(storage, "db", database)
    monkeypatch.setattr(auth_routes, "db", database)
    monkeypatch.setattr(api_mod, "db", database)
    return TestClient(api_mod.app), database, user


def _login(client: TestClient):
    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "long-secure-password"},
    )
    assert response.status_code == 200
    return response


def test_login_sets_http_only_session_cookies_and_keeps_legacy_body(monkeypatch, tmp_path):
    client, _, user = _client_with_user(monkeypatch, tmp_path)

    response = _login(client)

    assert response.json()["access_token"]
    assert response.json()["user_id"] == user.id
    set_cookie = response.headers.get("set-cookie", "")
    assert "pv_access=" in set_cookie
    assert "pv_refresh=" in set_cookie
    assert "pv_csrf=" in set_cookie
    assert "HttpOnly" in set_cookie


def test_cookie_auth_can_read_me_but_unsafe_request_requires_csrf(monkeypatch, tmp_path):
    client, _, _ = _client_with_user(monkeypatch, tmp_path)
    _login(client)

    assert client.get("/auth/me").status_code == 200

    denied = client.patch(
        "/auth/me/password",
        json={"current_password": "long-secure-password", "new_password": "another-secure-password"},
    )
    assert denied.status_code == 403

    csrf = client.cookies.get("pv_csrf")
    allowed = client.patch(
        "/auth/me/password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": "long-secure-password", "new_password": "another-secure-password"},
    )
    assert allowed.status_code == 200


def test_refresh_rotates_refresh_token(monkeypatch, tmp_path):
    client, database, _ = _client_with_user(monkeypatch, tmp_path)
    _login(client)
    old_refresh = client.cookies.get("pv_refresh")
    csrf = client.cookies.get("pv_csrf")

    denied = client.post("/auth/refresh")
    assert denied.status_code == 403

    response = client.post("/auth/refresh", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert client.cookies.get("pv_refresh") != old_refresh
    from petition_verifier.auth import hash_refresh_token

    old_row = database.get_refresh_token(hash_refresh_token(old_refresh))
    assert old_row is not None
    assert old_row.revoked_at is not None


def test_logout_revokes_refresh_token_and_clears_cookies(monkeypatch, tmp_path):
    client, database, _ = _client_with_user(monkeypatch, tmp_path)
    _login(client)
    refresh = client.cookies.get("pv_refresh")
    csrf = client.cookies.get("pv_csrf")

    denied = client.post("/auth/logout")
    assert denied.status_code == 403

    response = client.post("/auth/logout", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    assert not client.cookies.get("pv_access")
    assert not client.cookies.get("pv_refresh")
    from petition_verifier.auth import hash_refresh_token

    row = database.get_refresh_token(hash_refresh_token(refresh))
    assert row is not None
    assert row.revoked_at is not None


def test_legacy_bearer_token_still_works(monkeypatch, tmp_path):
    client, _, user = _client_with_user(monkeypatch, tmp_path)
    from petition_verifier.auth import create_token

    token = create_token(user.id, user.role)
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["email"] == "admin@example.com"
