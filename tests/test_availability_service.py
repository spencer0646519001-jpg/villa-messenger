"""
Tests for AvailabilityService.

The service is the anti-corruption boundary between Google-typed errors
and InquiryService. Tests use a stub client (a small class implementing
.fetch_events) — no mocks of googleapiclient itself, no network.
"""

import inspect
from datetime import date

import pytest

from app.clients.google_calendar_client import GoogleCalendarError
from app.domain.availability_models import AvailabilityResult, CalendarEvent
from app.services.availability_service import (
    AvailabilityCheckOutcome,
    AvailabilityService,
)


# ---------- Stubs ----------


class _StubClient:
    """Stand-in for GoogleCalendarClient. Records calls; returns canned events
    or raises a canned exception."""

    def __init__(
        self,
        *,
        events: list[CalendarEvent] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._events = events or []
        self._raises = raises
        self.calls: list[tuple[date, date]] = []

    def fetch_events(self, *, range_start: date, range_end: date) -> list[CalendarEvent]:
        self.calls.append((range_start, range_end))
        if self._raises is not None:
            raise self._raises
        return list(self._events)


def _event(summary: str, start: date, end: date) -> CalendarEvent:
    return CalendarEvent(summary=summary, start_date=start, end_date=end)


# ============================================================
# DISABLED PATH
# ============================================================


def test_disabled_returns_available_without_touching_client() -> None:
    client = _StubClient(events=[_event("枕123", date(2026, 5, 12), date(2026, 5, 13))])
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=False)

    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13))

    assert outcome.status == "available"
    assert outcome.result is None
    assert outcome.error_reason is None
    assert client.calls == []  # client never called when disabled


def test_disabled_still_returns_available_when_dates_would_be_blocked() -> None:
    client = _StubClient(events=[_event("枕123", date(2026, 5, 12), date(2026, 5, 13))])
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=False)

    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13))

    assert outcome.status == "available"
    assert client.calls == []


# ============================================================
# AVAILABLE PATH
# ============================================================


def test_enabled_with_no_events_returns_available() -> None:
    client = _StubClient(events=[])
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=True)

    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 14))

    assert outcome.status == "available"
    assert outcome.result is not None
    assert outcome.result.has_any_blocked_nights is False
    assert outcome.error_reason is None


def test_enabled_with_non_matching_event_returns_available() -> None:
    # "妃" is uncle's property — must not be treated as a blocking event.
    client = _StubClient(events=[_event("妃", date(2026, 5, 12), date(2026, 5, 13))])
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=True)

    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13))

    assert outcome.status == "available"
    assert outcome.result.has_any_blocked_nights is False


# ============================================================
# BLOCKED PATH
# ============================================================


def test_enabled_with_keyword_match_returns_blocked() -> None:
    client = _StubClient(events=[_event("枕123", date(2026, 5, 12), date(2026, 5, 13))])
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=True)

    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13))

    assert outcome.status == "blocked"
    assert outcome.result is not None
    assert outcome.result.has_any_blocked_nights is True
    assert len(outcome.result.blocked_nights) == 1
    bn = outcome.result.blocked_nights[0]
    assert bn.night_date == date(2026, 5, 12)
    assert bn.matched_keyword == "枕"


def test_real_title_variants_all_trigger_blocked() -> None:
    client = _StubClient(events=[
        _event("1房-枕123", date(2026, 5, 12), date(2026, 5, 13)),
        _event("枕123、妃", date(2026, 5, 13), date(2026, 5, 14)),
    ])
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=True)

    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 14))

    assert outcome.status == "blocked"
    assert len(outcome.result.blocked_nights) == 2


def test_mixed_blocking_and_non_blocking_events_still_blocked() -> None:
    client = _StubClient(events=[
        _event("妃", date(2026, 5, 12), date(2026, 5, 13)),
        _event("枕王", date(2026, 5, 13), date(2026, 5, 14)),
    ])
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=True)

    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 14))

    assert outcome.status == "blocked"
    # Only 5/13 is blocked (matched by "枕王"); 5/12 has only "妃" which doesn't match.
    assert len(outcome.result.blocked_nights) == 1
    assert outcome.result.blocked_nights[0].night_date == date(2026, 5, 13)


# ============================================================
# ERROR PATH — ANTI-CORRUPTION BOUNDARY
# ============================================================


def test_client_error_does_not_propagate_returns_error_outcome() -> None:
    # Critical invariant: GoogleCalendarError from the client must NEVER
    # escape AvailabilityService. It is translated to a status='error'
    # outcome so InquiryService stays Google-free and can fall back.
    client = _StubClient(raises=GoogleCalendarError("calendar fetch failed: HTTP 500"))
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=True)

    # Must NOT raise.
    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13))

    assert outcome.status == "error"
    assert outcome.error_reason is not None
    assert "HTTP 500" in outcome.error_reason
    assert outcome.result is None


def test_error_outcome_preserves_underlying_message() -> None:
    client = _StubClient(raises=GoogleCalendarError("calendar fetch failed: network down"))
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=True)

    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13))

    assert outcome.status == "error"
    assert "network down" in outcome.error_reason


def test_non_calendar_exceptions_still_propagate() -> None:
    # Only GoogleCalendarError is the anti-corruption boundary. A bug in the
    # client (e.g. AttributeError) should NOT be silently swallowed — that
    # would mask real defects.
    client = _StubClient(raises=AttributeError("internal bug"))
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=True)

    with pytest.raises(AttributeError, match="internal bug"):
        service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13))


# ============================================================
# CLIENT CALL SHAPE
# ============================================================


def test_client_called_with_correct_date_range() -> None:
    client = _StubClient(events=[])
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=True)

    service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 14))

    assert client.calls == [(date(2026, 5, 12), date(2026, 5, 14))]


def test_keywords_passed_to_check_availability() -> None:
    # If keywords were ignored, a "妃"-only calendar with keyword=["妃"]
    # would not return blocked. This test asserts keyword wiring works.
    client = _StubClient(events=[_event("妃", date(2026, 5, 12), date(2026, 5, 13))])
    service = AvailabilityService(client=client, booking_keywords=["妃"], enabled=True)

    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13))

    assert outcome.status == "blocked"


def test_keywords_list_copied_so_external_mutation_does_not_affect_service() -> None:
    keywords = ["枕"]
    client = _StubClient(events=[_event("枕123", date(2026, 5, 12), date(2026, 5, 13))])
    service = AvailabilityService(client=client, booking_keywords=keywords, enabled=True)

    keywords.clear()  # try to break the service externally

    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13))

    assert outcome.status == "blocked"


# ============================================================
# OUTCOME MODEL
# ============================================================


def test_outcome_default_fields_are_none() -> None:
    outcome = AvailabilityCheckOutcome(status="available")
    assert outcome.result is None
    assert outcome.error_reason is None


def test_outcome_carries_full_availability_result_on_block() -> None:
    client = _StubClient(events=[_event("枕123", date(2026, 5, 12), date(2026, 5, 13))])
    service = AvailabilityService(client=client, booking_keywords=["枕"], enabled=True)

    outcome = service.check(checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13))

    assert isinstance(outcome.result, AvailabilityResult)


# ============================================================
# METHOD-LENGTH DISCIPLINE
# ============================================================


def _body_line_count(func) -> int:
    src = inspect.getsource(func)
    lines = [line for line in src.splitlines()[1:] if line.strip() and not line.strip().startswith("#")]
    return len(lines)


@pytest.mark.parametrize(
    "func",
    [
        AvailabilityService.__init__,
        AvailabilityService.check,
        AvailabilityService._build_outcome,
    ],
)
def test_methods_under_15_body_lines(func) -> None:
    assert _body_line_count(func) <= 15, (
        f"{func.__qualname__} body too long: {_body_line_count(func)} lines"
    )
