"""
Environment reconstruction for one eval case: real tenant seeding, a deterministic
availability stub, and turn-by-turn timestamp/operation-mode reconstruction.

Every stub here is keyed off data already present in the case row (production_reference,
gold.expected_action) -- never off live Calendar/LINE/LLM, and never off
gold.expected_fields (which would hand the answer to the thing under test). See the
eval plan's "Key design decisions" section for the rationale on each of these.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.repositories.operation_state_repository import OperationStateRepository
from app.repositories.tenant_repository import TenantRepository
from app.services.operation_mode_service import OperationModeService

TENANT_SLUG = "zhen123-house"
TENANT_TIMEZONE = "Asia/Taipei"

# Anchor for the replay's "now": an arbitrary, fixed, daytime Taipei timestamp so
# night-window / stale-off-reconfirm logic behaves deterministically regardless of
# when the eval actually runs.
_ANCHOR_LOCAL = datetime(2026, 8, 17, 14, 0, tzinfo=ZoneInfo(TENANT_TIMEZONE))
ANCHOR_NOW_UTC = _ANCHOR_LOCAL.astimezone(timezone.utc)

_STALE_RECONFIRM_LABEL = "stale_context_reconfirm"


def seed_tenant(database_path: str | Path) -> int:
    """Create the one real tenant (zhen123-house) this eval replays against.
    Reuses the real, checked-in data/tenants/zhen123-house/config.json via the same
    loaders production uses -- read-only, no network."""
    return TenantRepository(database_path).create_tenant(
        slug=TENANT_SLUG,
        name=TENANT_SLUG.title(),
        timezone=TENANT_TIMEZONE,
        default_language="zh-TW",
        emergency_phone="0975-639-757",
    )


@dataclass(frozen=True)
class AvailabilityOutcome:
    status: str
    result: Any = None
    error_reason: str | None = None


class FakeAvailabilityService:
    """Deterministic AvailabilityServiceLike stub. Blocks any checkin date whose
    ISO string is in `blocked_checkin_dates` -- built per-case from the case's OWN
    gold.expected_action (see build_availability_service), never from live Calendar
    and never from gold.expected_fields."""

    enabled = True

    def __init__(self, blocked_checkin_dates: frozenset[str] = frozenset()) -> None:
        self._blocked = blocked_checkin_dates

    def check(self, *, checkin_date: date, checkout_date: date) -> AvailabilityOutcome:
        if checkin_date.isoformat() in self._blocked:
            return AvailabilityOutcome(status="blocked")
        return AvailabilityOutcome(status="available")


def build_availability_service(case: dict) -> FakeAvailabilityService:
    """A case is treated as a full-house scenario purely from its own
    gold.expected_action (an explicit, human-labeled ground truth about what this
    case is testing) -- sanctioned by the eval spec's section 4 ("use ... an isolated
    deterministic fake/stub according to the case's gold expectation"). Every other
    case gets an always-available calendar, matching evaluate_availability_gate's own
    default behavior when no calendar is configured."""
    expected_action = case.get("gold", {}).get("expected_action") or ""
    if "full_house" not in expected_action:
        return FakeAvailabilityService()
    checkin = (case.get("gold", {}).get("expected_fields") or {}).get("checkin_date")
    blocked = frozenset({checkin}) if checkin else frozenset()
    return FakeAvailabilityService(blocked)


def turn_timestamps(case: dict) -> list[datetime]:
    """One UTC timestamp per replayed turn (history..., final), oldest first.
    Consecutive turns are `session_gap_hours` apart (6h in this dataset), counting
    backward from ANCHOR_NOW_UTC so the FINAL turn always lands exactly on the
    anchor. The dataset gives no finer-grained real timestamps, so this is a
    documented approximation (eval plan decision 4)."""
    history = case.get("history") or []
    gap_hours = case.get("session_gap_hours", 6)
    total_turns = len(history) + 1
    return [
        ANCHOR_NOW_UTC - timedelta(hours=gap_hours * (total_turns - 1 - i))
        for i in range(total_turns)
    ]


def turn_operation_modes(case: dict) -> list[str]:
    """One of "on"/"off" per replayed turn (history..., final), matching
    turn_timestamps' ordering.

    Default: every turn (history and final alike) uses the CURRENT case row's own
    `production_reference.system_state_at_time` -- data already present per case, not
    fabricated. The one documented exception: cases whose gold.expected_action names
    the stale-context-reconfirm scenario, whose gold.notes explicitly describe an
    off-mode history followed by an on-mode current turn; the dataset has no
    per-history-turn state field, so history turns there are reconstructed as "off"
    and only the final turn uses the case's own declared state (eval plan decision 3).
    """
    history = case.get("history") or []
    final_mode = (case.get("production_reference") or {}).get("system_state_at_time") or "on"
    expected_action = case.get("gold", {}).get("expected_action") or ""
    history_mode = "off" if _STALE_RECONFIRM_LABEL in expected_action else final_mode
    return [history_mode] * len(history) + [final_mode]


def build_operation_mode_service(
    database_path: str | Path, *, now: datetime
) -> OperationModeService:
    """A fresh service bound to a fixed `now`. Callers call turn_on/turn_off
    immediately before replaying a turn (with this same service) so
    is_system_active() reflects that turn's reconstructed mode regardless of
    manual-override expiry windows computed at a different `now`."""
    return OperationModeService(
        repo=OperationStateRepository(database_path),
        now_provider=lambda: now,
    )


def apply_operation_mode(
    service: OperationModeService, *, tenant_id: int, mode: str
) -> None:
    if mode == "off":
        service.turn_off(tenant_id=tenant_id, tenant_timezone=TENANT_TIMEZONE)
    else:
        service.turn_on(tenant_id=tenant_id, tenant_timezone=TENANT_TIMEZONE)
