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


class ConversationStateService:
    def __init__(self, repo: ConversationStateRepository) -> None:
        self._repo = repo

    def record(self, *, message: InboundMessage, decision: InquiryDecision) -> None:
        """Merge this message's slots into the user's active state (or open one)."""
        slots = log_payload_to_state_slots(decision.log_payload)
        active = self._repo.get_active_for_user(
            tenant_id=message.tenant_id,
            platform=message.platform,
            platform_user_id=message.platform_user_id,
        )
        if active is None:
            self._open_if_inquiry(message, decision, slots)
        elif self._has_slot(slots):
            self._repo.update_slots(state_id=active["id"], **slots)

    def _open_if_inquiry(
        self, message: InboundMessage, decision: InquiryDecision, slots: dict
    ) -> None:
        intent = decision.log_payload.get("inquiry_intent")
        if decision.parsed_as_inquiry and intent in _QUOTE_RELEVANT_INTENTS:
            self._repo.create(
                tenant_id=message.tenant_id,
                platform=message.platform,
                platform_user_id=message.platform_user_id,
                **slots,
            )

    def _has_slot(self, slots: dict) -> bool:
        return any(slots.get(key) is not None for key in _SLOT_KEYS)
