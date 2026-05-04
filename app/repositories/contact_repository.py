from contextlib import closing
from pathlib import Path

from app.repositories._helpers import _row_to_dict, _utc_now_iso
from app.repositories.sqlite import get_connection


class ContactRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def get_or_create_contact(
        self,
        tenant_id: int,
        platform: str,
        platform_user_id: str,
        display_name: str | None = None,
        role: str = "guest",
    ) -> int:
        with closing(get_connection(self.database_path)) as connection:
            existing = connection.execute(
                """
                SELECT id
                FROM contacts
                WHERE tenant_id = ?
                  AND platform = ?
                  AND platform_user_id = ?
                LIMIT 1
                """,
                (tenant_id, platform, platform_user_id),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])

            now = _utc_now_iso()
            cursor = connection.execute(
                """
                INSERT INTO contacts (
                    tenant_id,
                    platform,
                    platform_user_id,
                    display_name,
                    role,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    platform,
                    platform_user_id,
                    display_name,
                    role,
                    now,
                    now,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def get_by_platform_user(
        self,
        tenant_id: int,
        platform: str,
        platform_user_id: str,
    ) -> dict | None:
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM contacts
                WHERE tenant_id = ?
                  AND platform = ?
                  AND platform_user_id = ?
                LIMIT 1
                """,
                (tenant_id, platform, platform_user_id),
            ).fetchone()

        return _row_to_dict(row)
