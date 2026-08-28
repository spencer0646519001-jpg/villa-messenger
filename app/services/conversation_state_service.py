"""
ConversationStateService — accumulate multi-turn inquiry slots into
conversation_states (STAGE B).

On each inbound message it merges the message's parsed slots into the user's
active conversation state so memory accumulates across turns. STAGE B records
state ONLY — it does not change what the bot replies (that is STAGE C).

Two-tier policy (the goldfish-memory fix):
  - OPEN (create) a state when the message is a quote-relevant inquiry, OR when
    it states a full checkin+checkout date range on its own -- a date range is
    strong enough booking-slot evidence to start tracking even when the
    message's own intent classification stays ambiguous (e.g. a reply like "是
    的\n訂8/2～8/4兩晚的" mid-conversation), so those dates aren't silently
    discarded for lack of anywhere to land. Pure chatter ("hi") still never
    creates a state.
  - UPDATE an existing active state from ANY slot-bearing follow-up, regardless
    of whether that message independently classifies as an inquiry — so a bare
    "4 adults" reply still fills the open state.
  - A message with no booking slots and no active state is a no-op (and a
    no-slot message against an active state does NOT refresh its TTL).

Unlike InquiryService (which is forbidden the repository layer), this service
MAY import repositories: it is the seam that keeps the webhook route thin.
"""

from datetime import datetime, timezone

from app.domain.inquiry_completeness import compute_missing_fields
from app.domain.inquiry_decision import InquiryDecision
from app.domain.log_payload_to_state_slots import log_payload_to_state_slots
from app.domain.pet_parser import parse_pet_count_answer
from app.domain.room_count_parser import parse_room_count_answer
from app.domain.text_normalizer import normalize_for_parsing
from app.repositories.conversation_state_repository import ConversationStateRepository
from app.schemas import InboundMessage

# Single source of truth for which intents are worth tracking; imported (not
# re-declared) so this create-gate cannot drift from the service's quote gate.
from app.services.inquiry_service import _QUOTE_RELEVANT_INTENTS


# Booking slots that count as "this message carried information". intent and
# last_message_text are excluded: they are present on essentially every message.
_SLOT_KEYS = (
    "checkin_date",
    "checkout_date",
    "adult_count",
    "child_count",
    "infant_count",
    "room_count",
    "pet_count",
    "has_pet",
    "wants_bbq",
)

# Column template for a freshly-created row, so the in-hand merged row mirrors a
# real DB row's shape (every column present, None when absent) -- STAGE C reads
# these keys directly. id is filled in by _open_if_inquiry.
_EMPTY_STATE_ROW: dict = {
    "id": None,
    "status": "in_progress",
    "intent": None,
    "checkin_date": None,
    "checkout_date": None,
    "adult_count": None,
    "child_count": None,
    "infant_count": None,
    "room_count": None,
    "pet_count": None,
    "has_pet": False,
    "wants_bbq": False,
    "last_message_text": None,
    "accumulated_while_off": False,
    "last_off_mode_update_at": None,
}


class ConversationStateService:
    def __init__(self, repo: ConversationStateRepository) -> None:
        self._repo = repo

    def record(self, *, message: InboundMessage, decision: InquiryDecision) -> dict | None:
        """Merge this message's slots into the user's active state (or open one)."""
        # Returns the merged in_progress row (or None) so STAGE C drives the reply
        # from the ACCUMULATED slots; assembled in-hand (mirrors the repo COALESCE),
        # no extra query.
        identity = _state_identity(message)
        self._repo.expire_stale_for_user(**identity)
        slots = log_payload_to_state_slots(decision.log_payload)
        off_kwargs = _off_flag_kwargs(decision)
        active = self._repo.get_active_for_user(**identity)
        if active is None:
            return self._open_if_inquiry(message, decision, slots, off_kwargs)
        return self._update_active(message, active, slots, off_kwargs)

    def _update_active(self, message: InboundMessage, active: dict, slots: dict, off_kwargs: dict) -> dict:
        self._fill_contextual_room_count(slots, active, message.text)
        self._fill_contextual_pet_count(slots, active, message.text)
        if not self._has_slot(slots):
            return active
        self._repo.update_slots(
            tenant_id=message.tenant_id, state_id=active["id"], **slots, **off_kwargs
        )
        return _merge_row(active, {**slots, **off_kwargs})

    def mark_completed(self, *, tenant_id: int, state_id: int) -> None:
        """Flip a state to completed (STAGE C, after a quote is sent)."""
        self._repo.mark_completed(tenant_id=tenant_id, state_id=state_id)

    def clear_accumulated_while_off(self, *, tenant_id: int, state_id: int) -> None:
        """Best-effort: called after the reply composer shows the Layer 2
        reconfirmation nudge once, so the next turn is treated as fresh."""
        self._repo.clear_accumulated_while_off(tenant_id=tenant_id, state_id=state_id)

    def _open_if_inquiry(
        self, message: InboundMessage, decision: InquiryDecision, slots: dict, off_kwargs: dict
    ) -> dict | None:
        if not _should_open(decision, slots):
            return None
        create_kwargs = {**slots, **_drop_none(off_kwargs)}
        state_id = self._repo.create(
            tenant_id=message.tenant_id,
            platform=message.platform,
            platform_user_id=message.platform_user_id,
            **create_kwargs,
        )
        return _merge_row({**_EMPTY_STATE_ROW, "id": state_id}, create_kwargs)

    def _has_slot(self, slots: dict) -> bool:
        return any(slots.get(key) is not None for key in _SLOT_KEYS)

    def _fill_contextual_room_count(self, slots: dict, active: dict, text: str) -> None:
        if slots.get("room_count") is not None or not _is_waiting_for_room_count(active):
            return
        slots["room_count"] = parse_room_count_answer(normalize_for_parsing(text))

    def _fill_contextual_pet_count(self, slots: dict, active: dict, text: str) -> None:
        if slots.get("pet_count") is not None or not _is_waiting_for_pet_count(active):
            return
        slots["pet_count"] = parse_pet_count_answer(normalize_for_parsing(text))


def _has_full_date_range(slots: dict) -> bool:
    return slots.get("checkin_date") is not None and slots.get("checkout_date") is not None


def _should_open(decision: InquiryDecision, slots: dict) -> bool:
    intent = decision.log_payload.get("inquiry_intent")
    is_quote_relevant = decision.parsed_as_inquiry and intent in _QUOTE_RELEVANT_INTENTS
    # Only a genuinely UNCLASSIFIED message (intent=="unknown") gets the
    # date-range bypass -- a message the pipeline confidently routed
    # elsewhere (faq, urgent, non_inquiry, ...) must not be reopened into
    # quote state just because it happens to also contain two dates, e.g.
    # "8/2到8/4有Wi-Fi嗎" (faq) or an urgent safety message that mentions
    # dates in passing. Flagged by Codex review of commit 3409642 (P2).
    is_ambiguous_with_dates = (
        intent == "unknown" and not decision.was_urgent and _has_full_date_range(slots)
    )
    return is_quote_relevant or is_ambiguous_with_dates


def _merge_row(base: dict, slots: dict) -> dict:
    """Apply non-None slots over a base row (mirrors the repo's COALESCE merge)."""
    merged = dict(base)
    for key, value in slots.items():
        if value is not None:
            merged[key] = value
    return merged


def _off_flag_kwargs(decision: InquiryDecision) -> dict:
    """None/None when this update happened while the system was ON (leaves
    whatever accumulated_while_off/last_off_mode_update_at the row already
    carries untouched -- e.g. a same-turn slot fill after the reconfirm nudge
    already fired stays cleared). True/now when it happened OFF (tenant-wide
    schedule off OR a Layer 1 per-customer pause both set was_system_off) --
    forces the flag on and the timestamp forward every off-mode touch, so a
    customer who keeps messaging while off has last_off_mode_update_at track
    their MOST RECENT off-mode message, not their first."""
    if not decision.was_system_off:
        return {"accumulated_while_off": None, "last_off_mode_update_at": None}
    return {
        "accumulated_while_off": True,
        "last_off_mode_update_at": datetime.now(timezone.utc).isoformat(),
    }


def _drop_none(values: dict) -> dict:
    return {key: value for key, value in values.items() if value is not None}


def _state_identity(message: InboundMessage) -> dict:
    return {
        "tenant_id": message.tenant_id,
        "platform": message.platform,
        "platform_user_id": message.platform_user_id,
    }


def _is_waiting_for_room_count(state: dict) -> bool:
    if state.get("room_count") is not None:
        return False
    return not compute_missing_fields(
        checkin_date=state["checkin_date"],
        checkout_date=state["checkout_date"],
        guest_count=_state_guest_count(state),
        has_pet=bool(state["has_pet"]),
        pet_count=state["pet_count"],
    )


def _is_waiting_for_pet_count(state: dict) -> bool:
    if not state.get("has_pet") or state.get("pet_count") is not None:
        return False
    return compute_missing_fields(
        checkin_date=state["checkin_date"],
        checkout_date=state["checkout_date"],
        guest_count=_state_guest_count(state),
        has_pet=bool(state["has_pet"]),
        pet_count=state["pet_count"],
    ) == ["pet_count"]


def _state_guest_count(state: dict) -> int | None:
    return ((state["adult_count"] or 0) + (state["child_count"] or 0)) or None
