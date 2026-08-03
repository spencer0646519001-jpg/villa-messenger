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
    room_count,
    pet_count,
    has_pet,
    wants_bbq,
    last_message_text,
    accumulated_while_off,
    last_off_mode_update_at,
    expires_at,
    created_at,
    updated_at
)
VALUES (?, ?, ?, 'in_progress', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    room_count = COALESCE(?, room_count),
    pet_count = COALESCE(?, pet_count),
    has_pet = COALESCE(?, has_pet),
    wants_bbq = COALESCE(?, wants_bbq),
    last_message_text = COALESCE(?, last_message_text),
    accumulated_while_off = COALESCE(?, accumulated_while_off),
    last_off_mode_update_at = COALESCE(?, last_off_mode_update_at),
    expires_at = CASE WHEN ? THEN ? ELSE expires_at END,
    updated_at = ?
WHERE id = ?
  AND tenant_id = ?
"""

_CLEAR_ACCUMULATED_WHILE_OFF_SQL = """
UPDATE conversation_states
SET accumulated_while_off = 0,
    updated_at = ?
WHERE id = ?
  AND tenant_id = ?
"""

_SET_STATUS_SQL = """
UPDATE conversation_states
SET status = ?, updated_at = ?
WHERE id = ?
  AND tenant_id = ?
"""

_EXPIRE_STALE_SQL = """
UPDATE conversation_states
SET status = 'expired', updated_at = ?
WHERE status = 'in_progress'
  AND expires_at <= ?
"""

_EXPIRE_STALE_FOR_USER_SQL = _EXPIRE_STALE_SQL + """
  AND tenant_id = ?
  AND platform = ?
  AND platform_user_id = ?
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
        room_count: int | None = None,
        has_pet: bool = False,
        pet_count: int | None = None,
        wants_bbq: bool = False,
        last_message_text: str | None = None,
        accumulated_while_off: bool = False,
        last_off_mode_update_at: str | None = None,
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
            room_count, pet_count, int(has_pet), int(wants_bbq), last_message_text,
            int(accumulated_while_off), last_off_mode_update_at, expires_at, now, now,
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
        tenant_id: int,
        state_id: int,
        intent: str | None = None,
        checkin_date: str | None = None,
        checkout_date: str | None = None,
        adult_count: int | None = None,
        child_count: int | None = None,
        infant_count: int | None = None,
        room_count: int | None = None,
        has_pet: bool | None = None,
        pet_count: int | None = None,
        wants_bbq: bool | None = None,
        last_message_text: str | None = None,
        accumulated_while_off: bool | None = None,
        last_off_mode_update_at: str | None = None,
        refresh_expiry: bool = True,
        ttl_hours: int = 24,
    ) -> None:
        """Merge non-None slots into the row (None leaves a slot unchanged);
        slide expires_at to now + ttl_hours when refresh_expiry is True.

        accumulated_while_off / last_off_mode_update_at follow the same
        None-means-untouched COALESCE convention: pass None from an on-mode
        update to leave whatever the row already carries; pass an explicit
        value (True + a fresh timestamp) from an off/paused update to force
        both forward -- see ConversationStateService._off_flag_kwargs."""
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        new_expires_at = (now_dt + timedelta(hours=ttl_hours)).isoformat()
        has_pet_param = None if has_pet is None else int(has_pet)
        wants_bbq_param = None if wants_bbq is None else int(wants_bbq)
        off_param = None if accumulated_while_off is None else int(accumulated_while_off)
        params = (
            intent, checkin_date, checkout_date, adult_count, child_count,
            infant_count, room_count, pet_count, has_pet_param, wants_bbq_param,
            last_message_text, off_param, last_off_mode_update_at,
            int(refresh_expiry), new_expires_at, now, state_id, tenant_id,
        )
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(_UPDATE_SLOTS_SQL, params)
            connection.commit()

    def clear_accumulated_while_off(self, *, tenant_id: int, state_id: int) -> None:
        """Best-effort clear after the reply composer has shown the
        reconfirmation nudge once (Layer 2) -- so the NEXT turn proceeds
        normally instead of nudging again."""
        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(_CLEAR_ACCUMULATED_WHILE_OFF_SQL, (now, state_id, tenant_id))
            connection.commit()

    def mark_completed(self, *, tenant_id: int, state_id: int) -> None:
        self._set_status(tenant_id, state_id, "completed")

    def mark_expired(self, *, tenant_id: int, state_id: int) -> None:
        self._set_status(tenant_id, state_id, "expired")

    def _set_status(self, tenant_id: int, state_id: int, status: str) -> None:
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(_SET_STATUS_SQL, (status, now, state_id, tenant_id))
            connection.commit()

    def expire_stale(self, *, tenant_id: int) -> int:
        """Bulk-flip every timed-out in_progress row to expired. Returns the
        number of rows changed. Always scoped to one tenant."""
        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            cursor = connection.execute(
                _EXPIRE_STALE_SQL + "  AND tenant_id = ?\n",
                (now, now, tenant_id),
            )
            connection.commit()
            return cursor.rowcount

    def expire_stale_for_user(
        self, *, tenant_id: int, platform: str, platform_user_id: str
    ) -> int:
        """Flip timed-out in_progress rows for exactly one platform user."""
        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            cursor = connection.execute(
                _EXPIRE_STALE_FOR_USER_SQL,
                (now, now, tenant_id, platform, platform_user_id),
            )
            connection.commit()
            return cursor.rowcount
