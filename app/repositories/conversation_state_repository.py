import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.repositories._helpers import _row_to_dict, _utc_now_iso
from app.repositories.sqlite import get_connection


_VALID_STATUSES = {"in_progress", "completed", "expired"}


_INSERT_STATE_SQL = """
INSERT INTO conversation_states (
    tenant_id,
    platform,
    platform_user_id,
    status,
    intent,
    checkin_date,
    checkout_date,
    adult_count,
    child_count,
    infant_count,
    pet_count,
    has_pet,
    last_message_text,
    expires_at,
    created_at,
    updated_at
)
VALUES (?, ?, ?, 'in_progress', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_GET_ACTIVE_SQL = """
SELECT *
FROM conversation_states
WHERE tenant_id = ?
  AND platform = ?
  AND platform_user_id = ?
  AND status = 'in_progress'
  AND expires_at > ?
LIMIT 1
"""

# COALESCE(?, col) => a None argument leaves the slot untouched; only non-None
# values overwrite. expires_at slides forward only when refresh_expiry is set.
_UPDATE_SLOTS_SQL = """
UPDATE conversation_states
SET intent = COALESCE(?, intent),
    checkin_date = COALESCE(?, checkin_date),
    checkout_date = COALESCE(?, checkout_date),
    adult_count = COALESCE(?, adult_count),
    child_count = COALESCE(?, child_count),
    infant_count = COALESCE(?, infant_count),
    pet_count = COALESCE(?, pet_count),
    has_pet = COALESCE(?, has_pet),
    last_message_text = COALESCE(?, last_message_text),
    expires_at = CASE WHEN ? THEN ? ELSE expires_at END,
    updated_at = ?
WHERE id = ?
"""

_SET_STATUS_SQL = """
UPDATE conversation_states
SET status = ?, updated_at = ?
WHERE id = ?
"""

_EXPIRE_STALE_SQL = """
UPDATE conversation_states
SET status = 'expired', updated_at = ?
WHERE status = 'in_progress'
  AND expires_at < ?
"""


class ConversationStateRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def get_active_for_user(
        self,
        *,
        tenant_id: int,
        platform: str,
        platform_user_id: str,
    ) -> dict | None:
        """Return the live in_progress state for this user, or None.

        Filters expires_at > now, so a stale (timed-out) row is never returned
        even before expire_stale flips its status."""
        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                _GET_ACTIVE_SQL,
                (tenant_id, platform, platform_user_id, now),
            ).fetchone()
        return _row_to_dict(row)

    def create(
        self,
        *,
        tenant_id: int,
        platform: str,
        platform_user_id: str,
        intent: str | None = None,
        checkin_date: str | None = None,
        checkout_date: str | None = None,
        adult_count: int | None = None,
        child_count: int | None = None,
        infant_count: int | None = None,
        has_pet: bool = False,
        pet_count: int | None = None,
        last_message_text: str | None = None,
        ttl_hours: int = 24,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """Insert a fresh in_progress row; expires_at = now + ttl_hours."""
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(hours=ttl_hours)).isoformat()
        params = (
            tenant_id, platform, platform_user_id, intent,
            checkin_date, checkout_date, adult_count, child_count, infant_count,
            pet_count, int(has_pet), last_message_text, expires_at, now, now,
        )
        if connection is not None:
            return self._insert_state(connection, params)
        with closing(get_connection(self.database_path)) as own_connection:
            row_id = self._insert_state(own_connection, params)
            own_connection.commit()
            return row_id

    def _insert_state(
        self,
        connection: sqlite3.Connection,
        params: tuple,
    ) -> int:
        cursor = connection.execute(_INSERT_STATE_SQL, params)
        return int(cursor.lastrowid)

    def update_slots(
        self,
        *,
        state_id: int,
        intent: str | None = None,
        checkin_date: str | None = None,
        checkout_date: str | None = None,
        adult_count: int | None = None,
        child_count: int | None = None,
        infant_count: int | None = None,
        has_pet: bool | None = None,
        pet_count: int | None = None,
        last_message_text: str | None = None,
        refresh_expiry: bool = True,
        ttl_hours: int = 24,
    ) -> None:
        """Merge non-None slots into the row (None leaves a slot unchanged);
        slide expires_at to now + ttl_hours when refresh_expiry is True."""
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        new_expires_at = (now_dt + timedelta(hours=ttl_hours)).isoformat()
        has_pet_param = None if has_pet is None else int(has_pet)
        params = (
            intent, checkin_date, checkout_date, adult_count, child_count,
            infant_count, pet_count, has_pet_param, last_message_text,
            int(refresh_expiry), new_expires_at, now, state_id,
        )
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(_UPDATE_SLOTS_SQL, params)
            connection.commit()

    def mark_completed(self, *, state_id: int) -> None:
        self._set_status(state_id, "completed")

    def mark_expired(self, *, state_id: int) -> None:
        self._set_status(state_id, "expired")

    def _set_status(self, state_id: int, status: str) -> None:
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(_SET_STATUS_SQL, (status, now, state_id))
            connection.commit()

    def expire_stale(self, *, tenant_id: int | None = None) -> int:
        """Bulk-flip every timed-out in_progress row to expired. Returns the
        number of rows changed. Scoped to one tenant when tenant_id is given."""
        now = _utc_now_iso()
        sql = _EXPIRE_STALE_SQL
        params: list = [now, now]
        if tenant_id is not None:
            sql += "  AND tenant_id = ?\n"
            params.append(tenant_id)
        with closing(get_connection(self.database_path)) as connection:
            cursor = connection.execute(sql, params)
            connection.commit()
            return cursor.rowcount
