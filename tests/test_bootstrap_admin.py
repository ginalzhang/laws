from __future__ import annotations

from types import SimpleNamespace


def test_bootstrap_admin_requires_email_and_password(monkeypatch):
    from petition_verifier.api import bootstrap_admin_from_env

    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)

    try:
        bootstrap_admin_from_env(SimpleNamespace())
    except RuntimeError as exc:
        assert "Set both" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError")


def test_bootstrap_admin_requires_strong_password(monkeypatch):
    from petition_verifier.api import bootstrap_admin_from_env

    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "short")

    try:
        bootstrap_admin_from_env(SimpleNamespace())
    except RuntimeError as exc:
        assert "at least 12" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError")


def test_bootstrap_admin_creates_missing_boss_once(monkeypatch):
    from petition_verifier.api import bootstrap_admin_from_env

    created = []

    class FakeDb:
        def __init__(self):
            self.exists = False

        def get_user_by_email(self, email):
            return SimpleNamespace(email=email) if self.exists else None

        def create_user(self, email, password_hash, role, full_name):
            created.append((email, role, full_name, password_hash))
            self.exists = True

    fake_db = FakeDb()
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "long-secure-password")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_NAME", "Admin User")

    assert bootstrap_admin_from_env(fake_db) is True
    assert bootstrap_admin_from_env(fake_db) is False
    assert len(created) == 1
    assert created[0][0] == "admin@example.com"
    assert created[0][1] == "boss"
    assert created[0][2] == "Admin User"
    assert created[0][3] != "long-secure-password"
