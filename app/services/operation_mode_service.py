from datetime import datetime, time, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from app.domain.operation_mode_resolver import (
    OperationStateSnapshot,
    compute_next_schedule_boundary,
    resolve_effective_mode,
)
from app.repositories.operation_state_repository import OperationStateRepository


def _row_to_snapshot(row: dict) -> OperationStateSnapshot:
    raw_valid_until = row.get("manual_valid_until")
    return OperationStateSnapshot(
        auto_schedule_enabled=bool(row["auto_schedule_enabled"]),
        auto_on_start_time=time.fromisoformat(row["auto_on_start_time"]),
        auto_on_end_time=time.fromisoformat(row["auto_on_end_time"]),
        manual_mode=row.get("manual_mode"),
        manual_valid_until=(
            datetime.fromisoformat(raw_valid_until)
            if raw_valid_until is not None
            else None
        ),
    )


class OperationModeService:
    def __init__(
        self,
        *,
        repo: OperationStateRepository,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repo
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def is_system_active(self, *, tenant_id: int, tenant_timezone: str) -> bool:
        """Return True if system should reply. Clears expired manual override as a side effect."""
        snapshot, now = self._load_state_with_expiry_cleanup(tenant_id, tenant_timezone)
        return resolve_effective_mode(state=snapshot, now=now) == "on"

    def turn_on(self, *, tenant_id: int, tenant_timezone: str, by_owner_id: int | None = None) -> None:
        self._set_manual_mode(tenant_id, tenant_timezone, "on", by_owner_id)

    def turn_off(self, *, tenant_id: int, tenant_timezone: str, by_owner_id: int | None = None) -> None:
        self._set_manual_mode(tenant_id, tenant_timezone, "off", by_owner_id)

    def clear_manual(self, *, tenant_id: int) -> None:
        """Idempotently creates the state row if missing."""
        self._repo.get_or_create(tenant_id)
        self._repo.clear_manual_override(tenant_id=tenant_id)

    def disable_schedule(self, *, tenant_id: int) -> None:
        """Idempotently creates the state row if missing."""
        self._repo.get_or_create(tenant_id)
        self._repo.set_schedule_enabled(tenant_id=tenant_id, enabled=False)

    def enable_schedule(self, *, tenant_id: int) -> None:
        """Idempotently creates the state row if missing."""
        self._repo.get_or_create(tenant_id)
        self._repo.set_schedule_enabled(tenant_id=tenant_id, enabled=True)

    def set_schedule_window(self, *, tenant_id: int, start_hhmm: str, end_hhmm: str) -> None:
        """Idempotently creates the state row if missing."""
        self._repo.get_or_create(tenant_id)
        self._repo.set_schedule_window(tenant_id=tenant_id, start_hhmm=start_hhmm, end_hhmm=end_hhmm)

    def _load_state_with_expiry_cleanup(
        self,
        tenant_id: int,
        tenant_timezone: str,
    ) -> tuple[OperationStateSnapshot, datetime]:
        row = self._repo.get_or_create(tenant_id)
        now = self._now_provider().astimezone(ZoneInfo(tenant_timezone))
        snapshot = _row_to_snapshot(row)
        if snapshot.manual_valid_until is not None and now >= snapshot.manual_valid_until:
            self._repo.clear_manual_override(tenant_id=tenant_id)
            snapshot = snapshot.model_copy(update={"manual_mode": None, "manual_valid_until": None})
        return snapshot, now

    def _set_manual_mode(
        self,
        tenant_id: int,
        tenant_timezone: str,
        mode: str,
        by_owner_id: int | None,
    ) -> None:
        snapshot, now = self._load_state_with_expiry_cleanup(tenant_id, tenant_timezone)
        valid_until = compute_next_schedule_boundary(
            start_time=snapshot.auto_on_start_time,
            end_time=snapshot.auto_on_end_time,
            now=now,
        )
        self._repo.set_manual_override(
            tenant_id=tenant_id,
            mode=mode,
            valid_until_iso=valid_until.isoformat(),
            owner_id=by_owner_id,
        )
