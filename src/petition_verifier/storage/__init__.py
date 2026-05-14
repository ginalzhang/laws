import os

from .database import Database, init_db

# Shared singleton — all routes import this instead of creating their own instance.
# Alembic imports the models without needing a live database connection.
db = None if os.getenv("PETITION_VERIFIER_SKIP_DB_INIT") == "true" else Database()

__all__ = ["Database", "init_db", "db"]
