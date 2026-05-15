from __future__ import annotations

import pytest
from alembic.config import Config
from typer.testing import CliRunner

from alembic import command


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    command.upgrade(Config("alembic.ini"), "head")

    import petition_verifier.storage as storage  # noqa: PLC0415

    storage.db.reset()
    return db_path


def test_admin_create_user_creates_hashed_login_user(migrated_db):
    from petition_verifier.auth import verify_password
    from petition_verifier.cli.main import app
    from petition_verifier.storage import Database

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "admin",
            "create-user",
            "new@example.com",
            "field_manager",
            "--full-name",
            "New Manager",
            "--phone",
            "555-0100",
            "--password",
            "secret123",
        ],
    )

    assert result.exit_code == 0, result.output

    user = Database().get_user_by_email("new@example.com")
    assert user is not None
    assert user.full_name == "New Manager"
    assert user.phone == "555-0100"
    assert user.role == "field_manager"
    assert user.password_hash != "secret123"
    assert verify_password("secret123", user.password_hash)


def test_admin_create_user_rejects_duplicate_email(migrated_db):
    from petition_verifier.cli.main import app

    runner = CliRunner()
    args = [
        "admin",
        "create-user",
        "dupe@example.com",
        "worker",
        "--password",
        "secret123",
    ]

    first = runner.invoke(app, args)
    second = runner.invoke(app, args)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 1
    assert "already exists" in second.output


def test_admin_create_user_rejects_invalid_role(migrated_db):
    from petition_verifier.cli.main import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "admin",
            "create-user",
            "bad-role@example.com",
            "superuser",
            "--password",
            "secret123",
        ],
    )

    assert result.exit_code == 1
    assert "Invalid role" in result.output
