"""Unit tests for log_payload_to_state_slots (STAGE B translator)."""

from app.domain.log_payload_to_state_slots import log_payload_to_state_slots


def _payload(**overrides: object) -> dict:
    """A log_payload with all parsed_* fields None (the _build_base default)."""
    base = {
        "raw_text": "5/12 入住",
        "inquiry_intent": None,
        "parsed_checkin": None,
        "parsed_checkout": None,
        "parsed_adult_count": None,
        "parsed_child_count": None,
        "parsed_infant_count": None,
        "parsed_room_count": None,
        "parsed_pet_count": None,
        "parsed_has_pet": None,
        "parsed_wants_bbq": None,
    }
    base.update(overrides)
    return base


def test_parsed_fields_map_to_slot_names() -> None:
    slots = log_payload_to_state_slots(
        _payload(
            inquiry_intent="price",
            parsed_checkin="2026-05-12",
            parsed_checkout="2026-05-14",
            parsed_adult_count=4,
            parsed_child_count=1,
            parsed_infant_count=2,
            parsed_room_count=3,
        )
    )

    assert slots["intent"] == "price"
    assert slots["checkin_date"] == "2026-05-12"
    assert slots["checkout_date"] == "2026-05-14"
    assert slots["adult_count"] == 4
    assert slots["child_count"] == 1
    assert slots["infant_count"] == 2
    assert slots["room_count"] == 3


def test_last_message_text_is_raw_text() -> None:
    slots = log_payload_to_state_slots(_payload(raw_text="4 大人"))

    assert slots["last_message_text"] == "4 大人"


def test_none_slots_stay_none() -> None:
    slots = log_payload_to_state_slots(_payload())

    for key in ("checkin_date", "checkout_date", "adult_count",
                "child_count", "infant_count", "room_count", "pet_count", "intent"):
        assert slots[key] is None


def test_has_pet_omitted_when_message_says_nothing_about_pets() -> None:
    # Omitted (not False) so update_slots' COALESCE never clobbers an existing
    # has_pet flag from a message that says nothing about pets.
    slots = log_payload_to_state_slots(_payload(parsed_has_pet=None, parsed_pet_count=None))

    assert "has_pet" not in slots
    assert slots["pet_count"] is None


def test_has_pet_true_when_pet_count_positive() -> None:
    slots = log_payload_to_state_slots(_payload(parsed_has_pet=True, parsed_pet_count=2))

    assert slots["has_pet"] is True
    assert slots["pet_count"] == 2


def test_has_pet_true_even_when_pet_count_still_unknown() -> None:
    # "有養狗" (has a dog, no count given yet) -- must still flip has_pet so
    # the state asks for the count, instead of silently staying pet-free.
    slots = log_payload_to_state_slots(_payload(parsed_has_pet=True, parsed_pet_count=None))

    assert slots["has_pet"] is True
    assert slots["pet_count"] is None


def test_has_pet_false_and_pet_count_cleared_when_explicitly_negated() -> None:
    # parsed_has_pet=False is tri-state: it only appears when the message
    # EXPLICITLY negated pets ("沒有寵物"), never for a message that just
    # didn't mention pets (that case is parsed_has_pet=None, see above).
    # An explicit negation must overwrite has_pet AND reset pet_count to 0,
    # otherwise a stale pet_count from an earlier turn keeps charging the
    # pet fee even after the customer took the pet back.
    slots = log_payload_to_state_slots(_payload(parsed_has_pet=False, parsed_pet_count=None))

    assert slots["has_pet"] is False
    assert slots["pet_count"] == 0


def test_wants_bbq_true_when_positively_mentioned() -> None:
    slots = log_payload_to_state_slots(_payload(parsed_wants_bbq=True))

    assert slots["wants_bbq"] is True


def test_wants_bbq_omitted_when_message_says_nothing_about_bbq() -> None:
    slots = log_payload_to_state_slots(_payload(parsed_wants_bbq=None))

    assert "wants_bbq" not in slots


def test_wants_bbq_false_when_explicitly_negated() -> None:
    # Same tri-state contract as has_pet: parsed_wants_bbq=False only occurs
    # on an explicit negation ("不要烤肉") and must overwrite the slot.
    slots = log_payload_to_state_slots(_payload(parsed_wants_bbq=False))

    assert slots["wants_bbq"] is False
