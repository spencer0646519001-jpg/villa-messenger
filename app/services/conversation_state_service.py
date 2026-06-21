"""
ConversationStateService — accumulate multi-turn inquiry slots into
conversation_states (STAGE B).

On each inbound message it merges the message's parsed slots into the user's
active conversation state so memory accumulates across turns. STAGE B records
state ONLY — it does not change what the bot replies (that is STAGE C).

Two-tier policy (the goldfish-memory fix):
  - OPEN (create) a state only when the message is a quote-relevant inquiry, so
    pure chatter ("hi") never creates a state.
  - UPDATE an existing active state from ANY slot-bearing follow-up, regardless
    of whether that message independently classifies as an inquiry — so a bare
    "4 adults" reply still fills the open state.
  - A message with no booking slots and no active state is a no-op (and a
    no-slot message against an active state does NOT refresh its TTL).

Unlike InquiryService (which is forbidden the repository layer), this service
MAY import repositories: it is the seam that keeps the webhook route thin.
"""

from app.domain.inquiry_decision import InquiryDecision
from app.domain.log_payload_to_state_slots import log_payload_to_state_slots
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
    "pet_count",
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
    "pet_count": None,
    "has_pet": False,
    "last_message_text": None,
}


class ConversationStateService:
    def __init__(self, repo: ConversationStateRepository) -> None:
        self._repo = repo

    def record(
        self, *, message: InboundMessage, decision: InquiryDecision
    ) -> dict | None:
        """Merge this message's slots into the user's active state (or open one)."""
        # Returns the merged in_progress row (or None) so STAGE C drives the reply
        # from the ACCUMULATED slots; assembled in-hand (mirrors the repo COALESCE),
        # no extra query.
        slots = log_payload_to_state_slots(decision.log_payload)
        active = self._repo.get_active_for_user(
            tenant_id=message.tenant_id,
            platform=message.platform,
            platform_user_id=message.platform_user_id,
        )
        if active is None:
            return self._open_if_inquiry(message, decision, slots)
        if self._has_slot(slots):
            self._repo.update_slots(tenant_id=message.tenant_id, state_id=active["id"], **slots)
            return _merge_row(active, slots)
        return active

    def mark_completed(self, *, tenant_id: int, state_id: int) -> None:
        """Flip a state to completed (STAGE C, after a quote is sent)."""
        self._repo.mark_completed(tenant_id=tenant_id, state_id=state_id)

    def _open_if_inquiry(
        self, message: InboundMessage, decision: InquiryDecision, slots: dict
    ) -> dict | None:
        intent = decision.log_payload.get("inquiry_intent")
        if not (decision.parsed_as_inquiry and intent in _QUOTE_RELEVANT_INTENTS):
            return None
        state_id = self._repo.create(
            tenant_id=message.tenant_id,
            platform=message.platform,
            platform_user_id=message.platform_user_id,
            **slots,
        )
        return _merge_row({**_EMPTY_STATE_ROW, "id": state_id}, slots)

    def _has_slot(self, slots: dict) -> bool:
        return any(slots.get(key) is not None for key in _SLOT_KEYS)


def _merge_row(base: dict, slots: dict) -> dict:
    """Apply non-None slots over a base row (mirrors the repo's COALESCE merge)."""
    merged = dict(base)
    for key, value in slots.items():
        if value is not None:
            merged[key] = value
    return merged
