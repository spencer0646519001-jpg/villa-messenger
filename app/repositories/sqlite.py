from contextlib import closing
import sqlite3
from pathlib import Path


def get_connection(database_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(database_path: str | Path) -> None:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")

    with closing(get_connection(path)) as connection:
        connection.executescript(schema_sql)
        connection.commit()
