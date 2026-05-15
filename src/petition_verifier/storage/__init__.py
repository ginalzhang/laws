from __future__ import annotations

from .database import Database, init_db


class _LazyDatabase:
    _instance: Database | None = None

    def _get(self) -> Database:
        if self._instance is None:
            self._instance = Database()
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._get(), name)

    def reset(self) -> None:
        self._instance = None


# Shared singleton proxy — all routes import this instead of creating their own instance.
db = _LazyDatabase()

__all__ = ["Database", "init_db", "db"]
