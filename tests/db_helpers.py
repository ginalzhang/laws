from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from petition_verifier.storage.database import Database


def migrated_database(db_path: Path) -> Database:
    url = f"sqlite:///{db_path}"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return Database(url)
