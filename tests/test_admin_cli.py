from __future__ import annotations

from typer.testing import CliRunner

from tests.db_helpers import migrated_database


def test_admin_create_user_dry_run_does_not_write(monkeypatch, tmp_path):
    db_path = tmp_path / "cli.db"
    migrated_database(db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from petition_verifier.cli.main import app

    result = CliRunner().invoke(
        app,
        [
            "admin",
            "create-user",
            "--email",
            "boss@example.com",
            "--full-name",
            "Boss User",
            "--role",
            "boss",
            "--password",
            "long-secure-password",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "Dry run OK" in result.output
    assert migrated_database(db_path).get_user_by_email("boss@example.com") is None


def test_admin_create_user_writes_hashed_password(monkeypatch, tmp_path):
    db_path = tmp_path / "cli-create.db"
    database = migrated_database(db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from petition_verifier.auth import verify_password
    from petition_verifier.cli.main import app

    result = CliRunner().invoke(
        app,
        [
            "admin",
            "create-user",
            "--email",
            "boss@example.com",
            "--full-name",
            "Boss User",
            "--role",
            "boss",
            "--password",
            "long-secure-password",
        ],
    )

    assert result.exit_code == 0
    user = database.get_user_by_email("boss@example.com")
    assert user is not None
    assert user.password_hash != "long-secure-password"
    assert verify_password("long-secure-password", user.password_hash)
