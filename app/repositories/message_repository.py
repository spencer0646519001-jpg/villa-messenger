import sqlite3
from contextlib import closing
from pathlib import Path

from app.repositories._helpers import _row_to_dict, _utc_now_iso
from app.repositories.sqlite import get_connection


_INSERT_MESSAGE_SQL = """
INSERT INTO messages (
    tenant_id,
    platform,
    platform_user_id,
    contact_id,
    reservation_id,
    customer_display_name,
    message_text,
    category,
    risk_level,
    reply_text,
    is_night,
    is_urgent,
    needs_manual_followup,
    send_alert_to_owner,
    handled,
    system_state_at_time,
    created_at,
    raw_log_payload
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


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
        customer_display_name: str | None = None,
        risk_level: int | None = None,
        reply_text: str | None = None,
        is_urgent: bool = False,
        needs_manual_followup: bool = False,
        send_alert_to_owner: bool = False,
        handled: bool = False,
        system_state_at_time: str = "unknown",
        raw_log_payload: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        params = (
            tenant_id,
            platform,
            platform_user_id,
            contact_id,
            reservation_id,
            customer_display_name,
            message_text,
            category,
            risk_level,
            reply_text,
            int(is_night),
            int(is_urgent),
            int(needs_manual_followup),
            int(send_alert_to_owner),
            int(handled),
            system_state_at_time,
            _utc_now_iso(),
            raw_log_payload,
        )
        if connection is not None:
            return self._insert_message(connection, params)
        with closing(get_connection(self.database_path)) as own_connection:
            row_id = self._insert_message(own_connection, params)
            own_connection.commit()
            return row_id

    def _insert_message(
        self,
        connection: sqlite3.Connection,
        params: tuple,
    ) -> int:
        cursor = connection.execute(_INSERT_MESSAGE_SQL, params)
        return int(cursor.lastrowid)

    def list_unhandled(self, tenant_id: int, limit: int = 20) -> list[dict]:
        """Excludes rows the owner already took ownership of via a handoff
        pause (system_state_at_time = 'paused_by_owner') at the SQL level --
        filtering those out in Python AFTER a LIMIT would risk dropping real
        pending rows off the end whenever the oldest `limit` rows happen to
        all be paused ones."""
        with closing(get_connection(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE tenant_id = ?
                  AND handled = 0
                  AND system_state_at_time != 'paused_by_owner'
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (tenant_id, limit),
            ).fetchall()

        return [dict(row) for row in rows]

    def list_between_created_at(
        self,
        tenant_id: int,
        start: str,
        end: str,
    ) -> list[dict]:
        with closing(get_connection(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT created_at, message_text, reply_text, platform_user_id
                FROM messages
                WHERE tenant_id = ?
                  AND created_at >= ?
                  AND created_at < ?
                ORDER BY created_at ASC, id ASC
                """,
                (tenant_id, start, end),
            ).fetchall()

        return [dict(row) for row in rows]

    def find_candidates_by_display_name(
        self, *, tenant_id: int, platform: str, display_name: str
    ) -> list[dict]:
        """One row per distinct platform_user_id that has ever sent a message
        with this exact display name, most-recently-active first. Used to
        resolve an owner's "/<display name>" pause command back to a
        platform_user_id (see ConversationHandoffService)."""
        with closing(get_connection(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT platform_user_id, MAX(created_at) AS last_message_at
                FROM messages
                WHERE tenant_id = ?
                  AND platform = ?
                  AND customer_display_name = ?
                GROUP BY platform_user_id
                ORDER BY last_message_at DESC
                """,
                (tenant_id, platform, display_name),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_unhandled(self, tenant_id: int, message_id: int) -> None:
        """Correct an optimistically-True `handled` flag back to 0 when the
        actual delivery attempt (customer reply and/or owner push) turned out
        to reach nobody, so the message reappears in list_unhandled / the
        nightly digest instead of being silently lost."""
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(
                "UPDATE messages SET handled = 0 WHERE tenant_id = ? AND id = ?",
                (tenant_id, message_id),
            )
            connection.commit()

    def mark_many_handled(self, tenant_id: int, message_ids: list[int]) -> None:
        """Close out backlog rows once they have actually been shown to the
        owner (via /待回覆 or the nightly digest), so they are not reported
        again on the next check."""
        if not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(
                f"UPDATE messages SET handled = 1 WHERE tenant_id = ? AND id IN ({placeholders})",
                (tenant_id, *message_ids),
            )
            connection.commit()

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
