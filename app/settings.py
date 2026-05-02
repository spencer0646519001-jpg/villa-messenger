from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "homestay-night-concierge"
    environment: str = "local"
    default_timezone: str = "Asia/Taipei"
    database_path: str = "data/homestay.db"


settings = Settings()

