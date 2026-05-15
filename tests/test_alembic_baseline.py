from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from alembic import command
from petition_verifier.storage.database import (
    Base,
    check_schema_current,
    has_unversioned_application_schema,
)


def _alembic_config() -> Config:
    root = Path(__file__).resolve().parents[1]
    return Config(str(root / "alembic.ini"))


def test_baseline_migration_matches_models(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'baseline.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    command.upgrade(_alembic_config(), "head")

    engine = create_engine(db_url)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diff = compare_metadata(context, Base.metadata)

    assert diff == []


def test_migration_config_works_outside_repo_cwd(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'outside-cwd.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.chdir(tmp_path)

    command.upgrade(_alembic_config(), "head")

    check_schema_current(db_url)


def test_unversioned_existing_schema_is_detected(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'unversioned.db'}"
    Base.metadata.create_all(create_engine(db_url))

    assert has_unversioned_application_schema(db_url)
    with pytest.raises(RuntimeError, match="Alembic head revision"):
        check_schema_current(db_url)


def test_baseline_downgrade_refuses_to_drop_schema(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'baseline-rollback.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    command.upgrade(_alembic_config(), "head")

    with pytest.raises(RuntimeError, match="Refusing to downgrade the baseline"):
        command.downgrade(_alembic_config(), "base")

    engine = create_engine(db_url)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diff = compare_metadata(context, Base.metadata)
    assert diff == []
