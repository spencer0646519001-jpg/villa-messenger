from contextlib import closing
from pathlib import Path

from app.repositories._helpers import _row_to_dict, _utc_now_iso
from app.repositories.sqlite import get_connection


class ManualHoldRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def get_active(
        self, *, tenant_id: int, platform: str, platform_user_id: str
    ) -> dict | None:
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM conversation_manual_holds
                WHERE tenant_id = ?
                  AND platform = ?
                  AND platform_user_id = ?
                LIMIT 1
                """,
                (tenant_id, platform, platform_user_id),
            ).fetchone()
        return _row_to_dict(row)

    def upsert_pause(
        self,
        *,
        tenant_id: int,
        platform: str,
        platform_user_id: str,
        paused_until_iso: str,
        owner_id: int | None,
    ) -> None:
        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(
                """
                INSERT INTO conversation_manual_holds (
                    tenant_id, platform, platform_user_id, paused_until,
                    created_by_owner_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, platform, platform_user_id) DO UPDATE SET
                    paused_until = excluded.paused_until,
                    created_by_owner_id = excluded.created_by_owner_id,
                    updated_at = excluded.updated_at
                """,
                (tenant_id, platform, platform_user_id, paused_until_iso, owner_id, now, now),
            )
            connection.commit()

    def clear(self, *, tenant_id: int, platform: str, platform_user_id: str) -> None:
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(
                """
                DELETE FROM conversation_manual_holds
                WHERE tenant_id = ? AND platform = ? AND platform_user_id = ?
                """,
                (tenant_id, platform, platform_user_id),
            )
            connection.commit()
