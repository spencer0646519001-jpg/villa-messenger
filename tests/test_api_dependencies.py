from app.api.dependencies import get_database_path
from app.settings import settings


def test_get_database_path_returns_settings_path() -> None:
    assert get_database_path() == settings.database_path
