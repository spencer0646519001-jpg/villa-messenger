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
        _ensure_column(connection, "conversation_states", "room_count", "INTEGER")
        _ensure_column(
            connection, "conversation_states", "accumulated_while_off", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(connection, "conversation_states", "last_off_mode_update_at", "TEXT")
        _ensure_column(
            connection, "conversation_states", "wants_bbq", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(connection, "messages", "customer_display_name", "TEXT")
        _ensure_column(connection, "tenant_operation_state", "last_digest_sent_date", "TEXT")
        connection.commit()


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_type: str,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
