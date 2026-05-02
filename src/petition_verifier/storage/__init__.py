from .database import Database, init_db

# Shared singleton — all routes import this instead of creating their own instance
db = Database()

__all__ = ["Database", "init_db", "db"]
