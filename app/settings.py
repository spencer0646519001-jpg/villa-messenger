import os
from dataclasses import dataclass, field


def _default_database_path() -> str:
    return os.environ.get("DATABASE_PATH", "data/homestay.db")


@dataclass(frozen=True)
class Settings:
    app_name: str = "homestay-night-concierge"
    environment: str = "local"
    default_timezone: str = "Asia/Taipei"
    database_path: str = field(default_factory=_default_database_path)


settings = Settings()
