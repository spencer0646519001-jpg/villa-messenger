from app.settings import Settings


def test_database_path_defaults_to_local_data_path(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_PATH", raising=False)

    assert Settings().database_path == "data/homestay.db"


def test_database_path_can_be_overridden_by_env(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", "/data/homestay.db")

    assert Settings().database_path == "/data/homestay.db"
