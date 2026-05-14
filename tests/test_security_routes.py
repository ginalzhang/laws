from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient


def _token_for(monkeypatch, role: str, user_id: int = 1) -> str:
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    import petition_verifier.auth as auth

    auth.SECRET_KEY = "test-secret-key"
    return auth.create_token(user_id, role)


def test_sensitive_project_routes_require_auth(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    from petition_verifier.api import app

    client = TestClient(app)

    assert client.get("/projects").status_code == 401
    assert client.get("/projects/abc/signatures").status_code == 401
    assert client.get("/projects/abc/export").status_code == 401
    assert client.post("/projects/abc/assign", json={"worker_id": 1}).status_code == 401


def test_worker_token_cannot_access_manager_project_list(monkeypatch):
    import petition_verifier.storage as storage
    from petition_verifier.api import app

    token = _token_for(monkeypatch, "boss")

    class FakeDb:
        def get_user_by_id(self, user_id):
            return SimpleNamespace(id=user_id, role="worker", is_active=True)

    monkeypatch.setattr(storage, "db", FakeDb())

    client = TestClient(app)
    res = client.get("/projects", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_dev_only_auth_shortcuts_are_disabled_by_default(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.delenv("DEV_AUTO_LOGIN", raising=False)
    from petition_verifier.api import app

    client = TestClient(app)

    assert client.post("/auth/login-by-name", json={"full_name": "Worker 1"}).status_code == 403
    assert client.get("/auth/active-users").status_code == 403
    assert client.post("/auth/scan-login", json={"password": "meow"}).status_code == 403
    assert client.post("/seed-demo-data").status_code == 403


def test_workers_only_see_their_own_review_packets(monkeypatch):
    import petition_verifier.storage as storage
    import petition_verifier.routes.review_routes as review_routes
    from petition_verifier.api import app

    token = _token_for(monkeypatch, "worker", user_id=1)

    class FakeAuthDb:
        def get_user_by_id(self, user_id):
            return SimpleNamespace(id=user_id, role="worker", is_active=True)

    class FakeReviewDb:
        def list_packets(self, worker_id=None):
            assert worker_id == 1
            return [
                SimpleNamespace(
                    id=1, original_name="mine.jpg", uploaded_at=None, status="done",
                    total_lines=1, new_sigs=1, already_counted=0, needs_review=0,
                    worker_id=1,
                )
            ]

        def get_packet_detail(self, packet_id):
            worker_id = 1 if packet_id == 1 else 2
            packet = SimpleNamespace(
                id=packet_id, original_name="packet.jpg", uploaded_at=None,
                status="done", error_msg="", total_lines=0, new_sigs=0,
                already_counted=0, needs_review=0, worker_id=worker_id,
                cleaned_path="", raw_path="", result_json="{}", voter_roll_text="",
                county="",
            )
            return packet, []

    monkeypatch.setattr(storage, "db", FakeAuthDb())
    monkeypatch.setattr(review_routes, "db", FakeReviewDb())

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/review/packets", headers=headers).json()[0]["worker_id"] == 1
    assert client.get("/review/packets/1", headers=headers).status_code == 200
    assert client.get("/review/packets/2", headers=headers).status_code == 403
    assert client.post("/review/packets/1/approve-all", headers=headers).status_code == 403


def test_reviewers_can_list_all_packets_and_approve(monkeypatch):
    import petition_verifier.storage as storage
    import petition_verifier.routes.review_routes as review_routes
    from petition_verifier.api import app

    token = _token_for(monkeypatch, "office_worker", user_id=9)

    class FakeAuthDb:
        def get_user_by_id(self, user_id):
            return SimpleNamespace(id=user_id, role="office_worker", is_active=True)

    class FakeReviewDb:
        def __init__(self):
            self.approved = False

        def list_packets(self, worker_id=None):
            assert worker_id is None
            return [
                SimpleNamespace(
                    id=1, original_name="packet.jpg", uploaded_at=None, status="done",
                    total_lines=1, new_sigs=1, already_counted=0, needs_review=0,
                    worker_id=2,
                )
            ]

        def get_packet_detail(self, packet_id):
            packet = SimpleNamespace(
                id=packet_id, original_name="packet.jpg", uploaded_at=None,
                status="done", error_msg="", total_lines=0, new_sigs=0,
                already_counted=0, needs_review=0, worker_id=2,
                cleaned_path="", raw_path="", result_json="{}", voter_roll_text="",
                county="",
            )
            return packet, []

        def approve_all_new_sigs(self, packet_id, reviewer_id):
            assert packet_id == 1
            assert reviewer_id == 9
            self.approved = True
            return 3

    fake_review_db = FakeReviewDb()
    monkeypatch.setattr(storage, "db", FakeAuthDb())
    monkeypatch.setattr(review_routes, "db", fake_review_db)

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/review/packets", headers=headers).json()[0]["worker_id"] == 2
    res = client.post("/review/packets/1/approve-all", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"approved": 3}
    assert fake_review_db.approved is True
