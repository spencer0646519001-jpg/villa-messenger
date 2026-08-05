"""
Translate an InquiryDecision.log_payload into the slot dict consumed by
ConversationStateRepository.create() / update_slots() (STAGE B).

This is the anti-corruption layer between the service-layer log_payload naming
(parsed_checkin, parsed_adult_count, ...) and the conversation_states column
naming (checkin_date, adult_count, ...). It mirrors decision_to_db_mapper but
targets the conversation_states table rather than messages/inquiries, so the
two mappers can evolve independently.

None slots stay None so update_slots' COALESCE leaves those columns untouched.
parsed_has_pet / parsed_wants_bbq are tri-state (True / False / None) coming
out of inquiry_service: None means this message never brought up pets/BBQ at
all (mentioned=False in the parser result) and must NOT clobber an existing
flag, while True/False are an explicit statement THIS turn ("有養狗" / "沒有
寵物") and must overwrite -- including clearing a stale pet_count back to 0
when the customer takes back a pet they mentioned earlier, otherwise the old
count would still be COALESCE'd through and keep charging the pet fee. has_pet
is deliberately NOT derived from pet_count alone: "有養狗" (has a dog, no
count given) must still set has_pet so the state asks for the count, instead
of silently staying pet-free because pet_count happens to be unresolved.
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
    has_pet = payload.get("parsed_has_pet")
    if has_pet is True:
        slots["has_pet"] = True
    elif has_pet is False:
        slots["has_pet"] = False
        slots["pet_count"] = 0
    wants_bbq = payload.get("parsed_wants_bbq")
    if wants_bbq is True:
        slots["wants_bbq"] = True
    elif wants_bbq is False:
        slots["wants_bbq"] = False
    return slots
