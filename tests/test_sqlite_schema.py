import sqlite3
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.repositories.sqlite import get_connection, init_db


EXPECTED_TABLES = {
    "tenants",
    "tenant_channels",
    "tenant_owners",
    "contacts",
    "reservations",
    "conversation_links",
    "messages",
    "inquiries",
    "tenant_operation_state",
}

NOW = "2026-05-03T00:00:00+08:00"


@pytest.fixture
def temp_db_dir() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-sqlite-schema")
    path = parent_dir / str(uuid.uuid4())
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            parent_dir.rmdir()
        except OSError:
            pass


def test_init_db_creates_sqlite_file(temp_db_dir: Path) -> None:
    database_path = temp_db_dir / "nested" / "homestay.db"

    init_db(database_path)

    assert database_path.is_file()


def test_init_db_creates_expected_tables(temp_db_dir: Path) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    with get_connection(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

    table_names = {row["name"] for row in rows}
    assert EXPECTED_TABLES <= table_names


def test_get_connection_returns_rows_accessible_by_name(temp_db_dir: Path) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    with get_connection(database_path) as connection:
        row = connection.execute("SELECT 123 AS answer").fetchone()

    assert row is not None
    assert row["answer"] == 123


def test_contacts_are_unique_per_tenant_and_platform(temp_db_dir: Path) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    with get_connection(database_path) as connection:
        tenant_a_id = _insert_tenant(connection, "tenant-a")
        tenant_b_id = _insert_tenant(connection, "tenant-b")
        _insert_contact(connection, tenant_a_id, "line", "user-1")
        _insert_contact(connection, tenant_b_id, "line", "user-1")
        _insert_contact(connection, tenant_a_id, "messenger", "user-1")

        with pytest.raises(sqlite3.IntegrityError):
            _insert_contact(connection, tenant_a_id, "line", "user-1")


def test_reservations_are_unique_per_tenant(temp_db_dir: Path) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    with get_connection(database_path) as connection:
        tenant_a_id = _insert_tenant(connection, "tenant-a")
        tenant_b_id = _insert_tenant(connection, "tenant-b")
        _insert_reservation(connection, tenant_a_id, "BOOK-123")
        _insert_reservation(connection, tenant_b_id, "BOOK-123")

        with pytest.raises(sqlite3.IntegrityError):
            _insert_reservation(connection, tenant_a_id, "BOOK-123")


def test_messages_table_includes_tenant_id(temp_db_dir: Path) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    assert "tenant_id" in _column_names(database_path, "messages")


def test_inquiries_table_includes_tenant_id(temp_db_dir: Path) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    assert "tenant_id" in _column_names(database_path, "inquiries")


def test_messages_table_includes_system_state_at_time_column(
    temp_db_dir: Path,
) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    assert "system_state_at_time" in _column_names(database_path, "messages")


def test_messages_system_state_at_time_defaults_to_unknown(
    temp_db_dir: Path,
) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    with get_connection(database_path) as connection:
        tenant_id = _insert_tenant(connection, "tenant-default")
        cursor = connection.execute(
            """
            INSERT INTO messages (
                tenant_id, platform, platform_user_id,
                message_text, category, is_night, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, "line", "guest-1", "hi", "question", 0, NOW),
        )
        message_id = cursor.lastrowid
        row = connection.execute(
            "SELECT system_state_at_time FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()

    assert row["system_state_at_time"] == "unknown"


def test_messages_system_state_at_time_is_not_null(temp_db_dir: Path) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    with get_connection(database_path) as connection:
        rows = connection.execute("PRAGMA table_info(messages)").fetchall()

    column = next(row for row in rows if row["name"] == "system_state_at_time")
    assert column["notnull"] == 1


def test_tenant_operation_state_table_exists(temp_db_dir: Path) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    with get_connection(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("tenant_operation_state",),
        ).fetchall()

    assert len(rows) == 1


def test_tenant_operation_state_has_expected_columns(temp_db_dir: Path) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    expected = {
        "tenant_id",
        "auto_schedule_enabled",
        "auto_on_start_time",
        "auto_on_end_time",
        "manual_mode",
        "manual_set_at",
        "manual_valid_until",
        "last_changed_by_owner_id",
        "updated_at",
    }
    assert expected <= _column_names(database_path, "tenant_operation_state")


def test_tenant_operation_state_primary_key_is_tenant_id(temp_db_dir: Path) -> None:
    database_path = temp_db_dir / "homestay.db"
    init_db(database_path)

    with get_connection(database_path) as connection:
        rows = connection.execute(
            "PRAGMA table_info(tenant_operation_state)"
        ).fetchall()

    pk_columns = {row["name"] for row in rows if row["pk"] > 0}
    assert pk_columns == {"tenant_id"}


def _insert_tenant(connection: sqlite3.Connection, slug: str) -> int:
    cursor = connection.execute(
        """
        INSERT INTO tenants (
            slug,
            name,
            timezone,
            default_language,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (slug, slug.title(), "Asia/Taipei", "zh-TW", NOW, NOW),
    )
    return int(cursor.lastrowid)


def _insert_contact(
    connection: sqlite3.Connection,
    tenant_id: int,
    platform: str,
    platform_user_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO contacts (
            tenant_id,
            platform,
            platform_user_id,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (tenant_id, platform, platform_user_id, NOW, NOW),
    )


def _insert_reservation(
    connection: sqlite3.Connection,
    tenant_id: int,
    booking_code: str,
) -> None:
    connection.execute(
        """
        INSERT INTO reservations (
            tenant_id,
            booking_code,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (tenant_id, booking_code, NOW, NOW),
    )


def _column_names(database_path: Path, table_name: str) -> set[str]:
    with get_connection(database_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()

    return {row["name"] for row in rows}
