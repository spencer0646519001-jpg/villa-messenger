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
        "parsed_pet_count": None,
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
        )
    )

    assert slots["intent"] == "price"
    assert slots["checkin_date"] == "2026-05-12"
    assert slots["checkout_date"] == "2026-05-14"
    assert slots["adult_count"] == 4
    assert slots["child_count"] == 1
    assert slots["infant_count"] == 2


def test_last_message_text_is_raw_text() -> None:
    slots = log_payload_to_state_slots(_payload(raw_text="4 大人"))

    assert slots["last_message_text"] == "4 大人"


def test_none_slots_stay_none() -> None:
    slots = log_payload_to_state_slots(_payload())

    for key in ("checkin_date", "checkout_date", "adult_count",
                "child_count", "infant_count", "pet_count", "intent"):
        assert slots[key] is None


def test_has_pet_omitted_when_pet_count_none() -> None:
    # Omitted (not False) so update_slots' COALESCE never clobbers an existing
    # has_pet flag from a message that says nothing about pets.
    slots = log_payload_to_state_slots(_payload(parsed_pet_count=None))

    assert "has_pet" not in slots
    assert slots["pet_count"] is None


def test_has_pet_true_when_pet_count_positive() -> None:
    slots = log_payload_to_state_slots(_payload(parsed_pet_count=2))

    assert slots["has_pet"] is True
    assert slots["pet_count"] == 2


def test_has_pet_false_when_pet_count_zero() -> None:
    slots = log_payload_to_state_slots(_payload(parsed_pet_count=0))

    assert slots["has_pet"] is False
    assert slots["pet_count"] == 0
