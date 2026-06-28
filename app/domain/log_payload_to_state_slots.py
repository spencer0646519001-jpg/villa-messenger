"""
Translate an InquiryDecision.log_payload into the slot dict consumed by
ConversationStateRepository.create() / update_slots() (STAGE B).

This is the anti-corruption layer between the service-layer log_payload naming
(parsed_checkin, parsed_adult_count, ...) and the conversation_states column
naming (checkin_date, adult_count, ...). It mirrors decision_to_db_mapper but
targets the conversation_states table rather than messages/inquiries, so the
two mappers can evolve independently.

None slots stay None so update_slots' COALESCE leaves those columns untouched.
has_pet is DERIVED from pet_count and included ONLY when pet_count is not None,
so a message that says nothing about pets never clobbers an existing has_pet
flag (omitting the key leaves it to create()'s default / update_slots' COALESCE).
"""


def log_payload_to_state_slots(payload: dict) -> dict:
    """Map log_payload parsed_* fields onto conversation_states slot kwargs."""
    pet_count = payload.get("parsed_pet_count")
    slots = {
        "intent": payload.get("inquiry_intent"),
        "checkin_date": payload.get("parsed_checkin"),
        "checkout_date": payload.get("parsed_checkout"),
        "adult_count": payload.get("parsed_adult_count"),
        "child_count": payload.get("parsed_child_count"),
        "infant_count": payload.get("parsed_infant_count"),
        "room_count": payload.get("parsed_room_count"),
        "pet_count": pet_count,
        "last_message_text": payload.get("raw_text"),
    }
    if pet_count is not None:
        slots["has_pet"] = pet_count > 0
    return slots
