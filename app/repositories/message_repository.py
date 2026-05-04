from contextlib import closing
from pathlib import Path

from app.repositories._helpers import _row_to_dict, _utc_now_iso
from app.repositories.sqlite import get_connection


class MessageRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def create_message(
        self,
        tenant_id: int,
        platform: str,
        platform_user_id: str,
        message_text: str,
        category: str,
        is_night: bool,
        contact_id: int | None = None,
        reservation_id: int | None = None,
        risk_level: int | None = None,
        reply_text: str | None = None,
        is_urgent: bool = False,
        needs_manual_followup: bool = False,
        send_alert_to_owner: bool = False,
        handled: bool = False,
    ) -> int:
        with closing(get_connection(self.database_path)) as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (
                    tenant_id,
                    platform,
                    platform_user_id,
                    contact_id,
                    reservation_id,
                    message_text,
                    category,
                    risk_level,
                    reply_text,
                    is_night,
                    is_urgent,
                    needs_manual_followup,
                    send_alert_to_owner,
                    handled,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    platform,
                    platform_user_id,
                    contact_id,
                    reservation_id,
                    message_text,
                    category,
                    risk_level,
                    reply_text,
                    int(is_night),
                    int(is_urgent),
                    int(needs_manual_followup),
                    int(send_alert_to_owner),
                    int(handled),
                    _utc_now_iso(),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_unhandled(self, tenant_id: int, limit: int = 20) -> list[dict]:
        with closing(get_connection(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE tenant_id = ?
                  AND handled = 0
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (tenant_id, limit),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_by_id(self, tenant_id: int, message_id: int) -> dict | None:
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE tenant_id = ?
                  AND id = ?
                LIMIT 1
                """,
                (tenant_id, message_id),
            ).fetchone()

        return _row_to_dict(row)
