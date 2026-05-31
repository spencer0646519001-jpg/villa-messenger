"""
FastAPI dependency providers shared by the API layer.

get_database_path is the single seam through which request handlers learn which
SQLite file to use. Production resolves it from settings; tests override it via
app.dependency_overrides[get_database_path] to point at a temp DB -- no global
state, no import-time DB binding.
"""

from app.settings import settings


def get_database_path() -> str:
    return settings.database_path
