from __future__ import annotations

import sqlite3

from alembic import command
from alembic.config import Config


def test_alembic_upgrade_head_creates_core_tables(tmp_path):
    db_path = tmp_path / "migrations.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("select name from sqlite_master where type='table'")
        }

    assert "alembic_version" in tables
    assert "projects" in tables
    assert "signatures" in tables
    assert "users" in tables
    assert "review_packets" in tables
    assert "review_packet_lines" in tables
    columns = {
        row[1]
        for row in conn.execute("pragma table_info(review_packet_lines)")
    }
    assert "low_confidence_fields" in columns
