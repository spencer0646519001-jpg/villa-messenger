import re
from contextlib import closing
from pathlib import Path

from app.repositories._helpers import _row_to_dict, _utc_now_iso
from app.repositories.sqlite import get_connection


_VALID_MODES = {"on", "off"}
_HHMM_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class OperationStateRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def get_or_create(self, tenant_id: int) -> dict:
        with closing(get_connection(self.database_path)) as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM tenant_operation_state
                WHERE tenant_id = ?
                LIMIT 1
                """,
                (tenant_id,),
            ).fetchone()
            if existing is not None:
                return dict(existing)

            now = _utc_now_iso()
            connection.execute(
                """
                INSERT INTO tenant_operation_state (
                    tenant_id,
                    auto_schedule_enabled,
                    auto_on_start_time,
                    auto_on_end_time,
                    manual_mode,
                    manual_set_at,
                    manual_valid_until,
                    last_changed_by_owner_id,
                    updated_at
                )
                VALUES (?, 1, '23:00', '08:00', NULL, NULL, NULL, NULL, ?)
                """,
                (tenant_id, now),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT *
                FROM tenant_operation_state
                WHERE tenant_id = ?
                LIMIT 1
                """,
                (tenant_id,),
            ).fetchone()

        return _row_to_dict(row)

    def set_manual_override(
        self,
        *,
        tenant_id: int,
        mode: str,
        valid_until_iso: str,
        owner_id: int | None,
    ) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f"invalid mode: {mode!r}")

        now = _utc_now_iso()
        # NOTE: manual_set_at intentionally captures *initial* set time.
        # Future cleanup logic (e.g. scheduled expiry job) must NOT overwrite
        # this — only updated_at should be touched on cleanup. See V2 admin UI work.
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(
                """
                UPDATE tenant_operation_state
                SET manual_mode = ?,
                    manual_set_at = ?,
                    manual_valid_until = ?,
                    last_changed_by_owner_id = ?,
                    updated_at = ?
                WHERE tenant_id = ?
                """,
                (mode, now, valid_until_iso, owner_id, now, tenant_id),
            )
            connection.commit()

    def clear_manual_override(self, *, tenant_id: int) -> None:
        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(
                """
                UPDATE tenant_operation_state
                SET manual_mode = NULL,
                    manual_set_at = NULL,
                    manual_valid_until = NULL,
                    updated_at = ?
                WHERE tenant_id = ?
                """,
                (now, tenant_id),
            )
            connection.commit()

    def set_schedule_enabled(
        self,
        *,
        tenant_id: int,
        enabled: bool,
    ) -> None:
        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(
                """
                UPDATE tenant_operation_state
                SET auto_schedule_enabled = ?,
                    updated_at = ?
                WHERE tenant_id = ?
                """,
                (int(enabled), now, tenant_id),
            )
            connection.commit()

    def mark_digest_sent(self, *, tenant_id: int, date_str: str) -> None:
        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(
                """
                UPDATE tenant_operation_state
                SET last_digest_sent_date = ?,
                    updated_at = ?
                WHERE tenant_id = ?
                """,
                (date_str, now, tenant_id),
            )
            connection.commit()

    def set_schedule_window(
        self,
        *,
        tenant_id: int,
        start_hhmm: str,
        end_hhmm: str,
    ) -> None:
        if not _HHMM_PATTERN.match(start_hhmm):
            raise ValueError(f"invalid start time: {start_hhmm!r}")
        if not _HHMM_PATTERN.match(end_hhmm):
            raise ValueError(f"invalid end time: {end_hhmm!r}")

        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(
                """
                UPDATE tenant_operation_state
                SET auto_on_start_time = ?,
                    auto_on_end_time = ?,
                    updated_at = ?
                WHERE tenant_id = ?
                """,
                (start_hhmm, end_hhmm, now, tenant_id),
            )
            connection.commit()
