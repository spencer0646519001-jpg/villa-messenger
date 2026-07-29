"""
ConversationHandoffService — per-customer manual "pause" (Layer 1 of the
23:00-boot-interrupt fix). Lets the owner tell the bot "I'm handling this one
customer myself" via a LINE command, addressed by the customer's LINE display
name (see app/api/line_webhook_routes.py's "/<display name>" toggle command).

DB-backed, mirrors OperationModeService's shape (manual override with an
expiry that rides the tenant's existing auto-schedule boundary).
"""

from datetime import datetime, time, timedelta, timezone
from typing import Callable, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.domain.conversation_handoff_resolver import ManualHoldSnapshot, is_paused
from app.domain.operation_mode_resolver import compute_next_active_window_end
from app.repositories.manual_hold_repository import ManualHoldRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.operation_state_repository import OperationStateRepository

# Two distinct-customer matches both active within this window count as a
# genuine naming collision (owner gets asked to disambiguate) rather than the
# service silently guessing the most-recently-active one.
_AMBIGUITY_WINDOW_HOURS = 48

ToggleAction = Literal["paused", "resumed"]
LookupStatus = Literal["found", "ambiguous", "not_found"]


class DisplayNameCandidate(BaseModel):
    platform_user_id: str
    last_message_at: str


class DisplayNameLookupResult(BaseModel):
    status: LookupStatus
    platform_user_id: str | None = None
    candidates: list[DisplayNameCandidate] = []


class ConversationHandoffService:
    def __init__(
        self,
        *,
        hold_repo: ManualHoldRepository,
        message_repo: MessageRepository,
        operation_state_repo: OperationStateRepository,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._holds = hold_repo
        self._messages = message_repo
        self._operation_state = operation_state_repo
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def is_paused(
        self, *, tenant_id: int, platform: str, platform_user_id: str
    ) -> bool:
        """Side effect: clears an expired hold row so it stops being checked."""
        row = self._holds.get_active(
            tenant_id=tenant_id, platform=platform, platform_user_id=platform_user_id
        )
        if row is None:
            return False
        snapshot = ManualHoldSnapshot(paused_until=datetime.fromisoformat(row["paused_until"]))
        now = self._now_provider()
        if is_paused(snapshot=snapshot, now=now):
            return True
        self._holds.clear(
            tenant_id=tenant_id, platform=platform, platform_user_id=platform_user_id
        )
        return False

    def resolve_by_display_name(
        self, *, tenant_id: int, platform: str, display_name: str
    ) -> DisplayNameLookupResult:
        rows = self._messages.find_candidates_by_display_name(
            tenant_id=tenant_id, platform=platform, display_name=display_name
        )
        if not rows:
            return DisplayNameLookupResult(status="not_found")
        now = self._now_provider()
        recent = [row for row in rows if _within_window(row["last_message_at"], now)]
        if len(recent) >= 2:
            return DisplayNameLookupResult(
                status="ambiguous",
                candidates=[DisplayNameCandidate(**row) for row in rows],
            )
        return DisplayNameLookupResult(status="found", platform_user_id=rows[0]["platform_user_id"])

    def toggle(
        self,
        *,
        tenant_id: int,
        tenant_timezone: str,
        platform: str,
        platform_user_id: str,
        owner_id: int | None = None,
    ) -> ToggleAction:
        """Pause if not currently paused, resume (delete the hold) if already
        paused. Pause expiry always lands on the next moment the auto-on
        window closes, so it covers the entire upcoming/current on-window
        regardless of whether it's toggled before or during it."""
        if self.is_paused(
            tenant_id=tenant_id, platform=platform, platform_user_id=platform_user_id
        ):
            self._holds.clear(
                tenant_id=tenant_id, platform=platform, platform_user_id=platform_user_id
            )
            return "resumed"
        state = self._operation_state.get_or_create(tenant_id)
        now_local = self._now_provider().astimezone(ZoneInfo(tenant_timezone))
        valid_until = compute_next_active_window_end(
            start_time=time.fromisoformat(state["auto_on_start_time"]),
            end_time=time.fromisoformat(state["auto_on_end_time"]),
            now=now_local,
        )
        self._holds.upsert_pause(
            tenant_id=tenant_id,
            platform=platform,
            platform_user_id=platform_user_id,
            paused_until_iso=valid_until.isoformat(),
            owner_id=owner_id,
        )
        return "paused"


def _within_window(iso_timestamp: str, now: datetime) -> bool:
    last = datetime.fromisoformat(iso_timestamp)
    return now - last <= timedelta(hours=_AMBIGUITY_WINDOW_HOURS)
