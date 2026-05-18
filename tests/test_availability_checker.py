import inspect
from datetime import date

import pytest

import app.domain.availability_checker as checker_module
from app.domain.availability_checker import check_availability
from app.domain.availability_models import (
    AvailabilityResult,
    BlockedNight,
    CalendarEvent,
)


def _event(summary: str, start: date, end: date) -> CalendarEvent:
    return CalendarEvent(summary=summary, start_date=start, end_date=end)


# ============================================================
# 1-5: BASIC BLOCKING
# ============================================================


def test_single_event_one_night_keyword_matches_blocks() -> None:
    events = [_event("枕王", date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕"],
    )
    assert result.has_any_blocked_nights is True
    assert len(result.blocked_nights) == 1
    bn = result.blocked_nights[0]
    assert bn.night_date == date(2026, 5, 12)
    assert bn.blocking_event_summary == "枕王"
    assert bn.matched_keyword == "枕"


def test_single_event_one_night_no_keyword_match_not_blocked() -> None:
    events = [_event("妃", date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕"],
    )
    assert result.has_any_blocked_nights is False
    assert result.blocked_nights == []


def test_no_events_at_all_not_blocked() -> None:
    result = check_availability(
        events=[],
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
        booking_keywords=["枕"],
    )
    assert result.has_any_blocked_nights is False
    assert result.blocked_nights == []


def test_event_outside_stay_window_not_blocked() -> None:
    # Stay nights: 5/12, 5/13. Event spans 5/20 only.
    events = [_event("枕123", date(2026, 5, 20), date(2026, 5, 21))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
        booking_keywords=["枕"],
    )
    assert result.has_any_blocked_nights is False


def test_event_spanning_multiple_stay_nights_blocks_each() -> None:
    # Stay nights: 5/12, 5/13, 5/14. Event covers 5/12-5/15 (nights 12,13,14).
    events = [_event("枕王", date(2026, 5, 12), date(2026, 5, 15))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 15),
        booking_keywords=["枕"],
    )
    assert result.has_any_blocked_nights is True
    assert len(result.blocked_nights) == 3
    assert [bn.night_date for bn in result.blocked_nights] == [
        date(2026, 5, 12),
        date(2026, 5, 13),
        date(2026, 5, 14),
    ]


# ============================================================
# 6-13: KEYWORD MATCHING
# ============================================================


@pytest.mark.parametrize(
    "summary",
    [
        "枕123",
        "2房-枕12X",
        "李倉維+枕123",
        "枕",
        "abc枕def",
    ],
)
def test_keyword_matches_as_substring_anywhere(summary: str) -> None:
    events = [_event(summary, date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕"],
    )
    assert result.has_any_blocked_nights is True


@pytest.mark.parametrize(
    "summary",
    [
        "妃",
        "Booking",
        "李倉維",
        "",
    ],
)
def test_keyword_does_not_match_unrelated_summary(summary: str) -> None:
    events = [_event(summary, date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕"],
    )
    assert result.has_any_blocked_nights is False


def test_multiple_keywords_second_one_matches() -> None:
    events = [_event("Booking-12345", date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕", "Booking"],
    )
    assert result.has_any_blocked_nights is True
    assert result.blocked_nights[0].matched_keyword == "Booking"


def test_multiple_keywords_first_one_matches_reports_first_order() -> None:
    # Summary contains BOTH keywords; first in list order wins.
    events = [_event("枕-Booking", date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕", "Booking"],
    )
    assert result.blocked_nights[0].matched_keyword == "枕"


def test_multiple_keywords_first_in_list_wins_when_only_second_in_summary() -> None:
    # Reversing list order produces "Booking" first.
    events = [_event("枕-Booking", date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["Booking", "枕"],
    )
    assert result.blocked_nights[0].matched_keyword == "Booking"


def test_empty_keywords_list_never_blocks() -> None:
    events = [_event("枕王", date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=[],
    )
    assert result.has_any_blocked_nights is False


# ============================================================
# 14-16: MULTI-EVENT
# ============================================================


def test_two_matching_events_same_night_produces_single_blocked_entry() -> None:
    events = [
        _event("枕A", date(2026, 5, 12), date(2026, 5, 13)),
        _event("枕B", date(2026, 5, 12), date(2026, 5, 13)),
    ]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕"],
    )
    assert len(result.blocked_nights) == 1
    # First event in list order wins.
    assert result.blocked_nights[0].blocking_event_summary == "枕A"


def test_two_matching_events_different_nights_produces_two_entries() -> None:
    events = [
        _event("枕A", date(2026, 5, 12), date(2026, 5, 13)),
        _event("枕B", date(2026, 5, 13), date(2026, 5, 14)),
    ]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
        booking_keywords=["枕"],
    )
    assert len(result.blocked_nights) == 2
    assert result.blocked_nights[0].blocking_event_summary == "枕A"
    assert result.blocked_nights[1].blocking_event_summary == "枕B"


def test_matching_plus_non_matching_event_same_night_blocked_by_matching() -> None:
    events = [
        _event("妃", date(2026, 5, 12), date(2026, 5, 13)),
        _event("枕A", date(2026, 5, 12), date(2026, 5, 13)),
    ]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕"],
    )
    assert len(result.blocked_nights) == 1
    assert result.blocked_nights[0].blocking_event_summary == "枕A"


# ============================================================
# 17-19: DATE BOUNDARIES
# ============================================================


def test_event_starting_on_checkin_date_blocks() -> None:
    events = [_event("枕A", date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
        booking_keywords=["枕"],
    )
    assert len(result.blocked_nights) == 1
    assert result.blocked_nights[0].night_date == date(2026, 5, 12)


def test_event_end_date_equals_checkout_does_not_block_checkout_itself() -> None:
    # Stay nights: 5/12, 5/13. Event covers nights 5/12, 5/13 (end=5/14 exclusive).
    # We just verify nothing leaks beyond — checkout date is never a night.
    events = [_event("枕A", date(2026, 5, 12), date(2026, 5, 14))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
        booking_keywords=["枕"],
    )
    assert len(result.blocked_nights) == 2
    blocked_dates = [bn.night_date for bn in result.blocked_nights]
    assert date(2026, 5, 14) not in blocked_dates


def test_event_with_end_date_equal_to_night_before_checkout_blocks() -> None:
    # Stay nights: 5/12, 5/13. Event 5/13-5/14 covers only night 5/13.
    events = [_event("枕A", date(2026, 5, 13), date(2026, 5, 14))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
        booking_keywords=["枕"],
    )
    assert len(result.blocked_nights) == 1
    assert result.blocked_nights[0].night_date == date(2026, 5, 13)


# ============================================================
# 20-21: STAY SHAPE
# ============================================================


def test_single_night_stay_blocked() -> None:
    events = [_event("枕A", date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕"],
    )
    assert len(result.blocked_nights) == 1


def test_partial_overlap_only_blocks_overlap_nights() -> None:
    # Stay nights: 5/13, 5/14. Event covers nights 5/12, 5/13.
    # Only 5/13 overlaps.
    events = [_event("枕A", date(2026, 5, 12), date(2026, 5, 14))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 13),
        checkout_date=date(2026, 5, 15),
        booking_keywords=["枕"],
    )
    assert len(result.blocked_nights) == 1
    assert result.blocked_nights[0].night_date == date(2026, 5, 13)


# ============================================================
# 22-23: VALIDATION
# ============================================================


def test_checkout_before_checkin_raises() -> None:
    with pytest.raises(ValueError, match="checkout_date"):
        check_availability(
            events=[],
            checkin_date=date(2026, 5, 14),
            checkout_date=date(2026, 5, 12),
            booking_keywords=["枕"],
        )


def test_checkout_equal_to_checkin_raises() -> None:
    with pytest.raises(ValueError, match="checkout_date"):
        check_availability(
            events=[],
            checkin_date=date(2026, 5, 12),
            checkout_date=date(2026, 5, 12),
            booking_keywords=["枕"],
        )


# ============================================================
# 24-26: RESULT INVARIANTS
# ============================================================


def test_no_blocked_result_has_empty_list() -> None:
    result = check_availability(
        events=[],
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕"],
    )
    assert result.has_any_blocked_nights is False
    assert result.blocked_nights == []


def test_blocked_result_has_non_empty_list() -> None:
    events = [_event("枕A", date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕"],
    )
    assert result.has_any_blocked_nights is True
    assert len(result.blocked_nights) > 0


def test_blocked_nights_preserve_chronological_order_with_gaps() -> None:
    # Two non-contiguous matching events. Pass them in REVERSE chronological
    # order in `events` to prove the result orders by night_date, not by
    # event input order.
    events = [
        _event("枕Late", date(2026, 5, 15), date(2026, 5, 16)),
        _event("枕Early", date(2026, 5, 12), date(2026, 5, 13)),
    ]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 17),
        booking_keywords=["枕"],
    )
    assert [bn.night_date for bn in result.blocked_nights] == [
        date(2026, 5, 12),
        date(2026, 5, 15),
    ]
    # Verify gap is preserved: 5/13 and 5/14 are not blocked.
    blocked_dates = {bn.night_date for bn in result.blocked_nights}
    assert date(2026, 5, 13) not in blocked_dates
    assert date(2026, 5, 14) not in blocked_dates


# ============================================================
# 27: DISCIPLINE
# ============================================================


def test_no_checker_function_exceeds_line_budget() -> None:
    """Public function ≤15 body lines; private helpers ≤10."""
    for name, obj in inspect.getmembers(checker_module, inspect.isfunction):
        if obj.__module__ != checker_module.__name__:
            continue
        source = inspect.getsource(obj)
        lines = [
            line
            for line in source.split("\n")
            if line.strip() and not line.strip().startswith('"""')
        ]
        body_lines = len(lines) - 1
        limit = 10 if name.startswith("_") else 15
        kind = "private" if name.startswith("_") else "public"
        assert body_lines <= limit, (
            f"{kind} checker function {name} has {body_lines} body lines, "
            f"max is {limit}"
        )


def test_input_events_list_not_mutated() -> None:
    events = [
        _event("枕A", date(2026, 5, 12), date(2026, 5, 13)),
        _event("妃", date(2026, 5, 13), date(2026, 5, 14)),
    ]
    snapshot = list(events)
    check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
        booking_keywords=["枕"],
    )
    assert events == snapshot


def test_input_keywords_list_not_mutated() -> None:
    keywords = ["枕", "Booking"]
    snapshot = list(keywords)
    check_availability(
        events=[_event("枕A", date(2026, 5, 12), date(2026, 5, 13))],
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=keywords,
    )
    assert keywords == snapshot


def test_result_is_availability_result_type() -> None:
    result = check_availability(
        events=[],
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕"],
    )
    assert isinstance(result, AvailabilityResult)


def test_blocked_night_is_correct_type() -> None:
    events = [_event("枕A", date(2026, 5, 12), date(2026, 5, 13))]
    result = check_availability(
        events=events,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        booking_keywords=["枕"],
    )
    assert isinstance(result.blocked_nights[0], BlockedNight)
