"""
Translate an InquiryDecision.log_payload into the slot dict consumed by
ConversationStateRepository.create() / update_slots() (STAGE B).

This is the anti-corruption layer between the service-layer log_payload naming
(parsed_checkin, parsed_adult_count, ...) and the conversation_states column
naming (checkin_date, adult_count, ...). It mirrors decision_to_db_mapper but
targets the conversation_states table rather than messages/inquiries, so the
two mappers can evolve independently.

None slots stay None so update_slots' COALESCE leaves those columns untouched.
has_pet/wants_bbq are included ONLY when this message positively mentioned
them (parsed_has_pet / parsed_wants_bbq is True), so a message that says
nothing about pets/bbq never clobbers an existing flag (omitting the key
leaves it to create()'s default / update_slots' COALESCE). has_pet is
deliberately NOT derived from pet_count alone: "有養狗" (has a dog, no count
given) must still set has_pet so the state asks for the count, instead of
silently staying pet-free because pet_count happens to be unresolved.
"""


def log_payload_to_state_slots(payload: dict) -> dict:
    """Map log_payload parsed_* fields onto conversation_states slot kwargs."""
    slots = {
        "intent": payload.get("inquiry_intent"),
        "checkin_date": payload.get("parsed_checkin"),
        "checkout_date": payload.get("parsed_checkout"),
        "adult_count": payload.get("parsed_adult_count"),
        "child_count": payload.get("parsed_child_count"),
        "infant_count": payload.get("parsed_infant_count"),
        "room_count": payload.get("parsed_room_count"),
        "pet_count": payload.get("parsed_pet_count"),
        "last_message_text": payload.get("raw_text"),
    }
    if payload.get("parsed_has_pet"):
        slots["has_pet"] = True
    if payload.get("parsed_wants_bbq"):
        slots["wants_bbq"] = True
    return slots
