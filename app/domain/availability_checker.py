"""
Pure-function availability checking.

Given a list of CalendarEvents and an inquiry date range, decide whether
any nights in the range overlap with any event whose summary contains a
booking keyword.

The function NEVER talks to Google. PR8.5b is responsible for fetching
events and translating them into CalendarEvent instances before calling
check_availability().
"""

from collections.abc import Iterator
from datetime import date, timedelta

from app.domain.availability_models import (
    AvailabilityResult,
    BlockedNight,
    CalendarEvent,
)


def check_availability(
    *,
    events: list[CalendarEvent],
    checkin_date: date,
    checkout_date: date,
    booking_keywords: list[str],
) -> AvailabilityResult:
    if checkout_date <= checkin_date:
        raise ValueError(f"checkout_date ({checkout_date}) must be after checkin_date ({checkin_date})")
    blocked = [
        b for night in _iter_nights(checkin_date, checkout_date)
        if (b := _first_blocking_event(events, night, booking_keywords)) is not None
    ]
    return AvailabilityResult(has_any_blocked_nights=bool(blocked), blocked_nights=blocked)


def _iter_nights(checkin: date, checkout: date) -> Iterator[date]:
    # Replicates pricing_policy._iter_nights (module-private there) to avoid
    # cross-module dependency on an internal helper. Keep in sync.
    current = checkin
    while current < checkout:
        yield current
        current = current + timedelta(days=1)


def _event_covers_night(event: CalendarEvent, night: date) -> bool:
    return event.start_date <= night < event.end_date


def _matched_keyword(summary: str, keywords: list[str]) -> str | None:
    for keyword in keywords:
        if keyword in summary:
            return keyword
    return None


def _first_blocking_event(events: list[CalendarEvent], night: date, keywords: list[str]) -> BlockedNight | None:
    for event in events:
        if not _event_covers_night(event, night):
            continue
        matched = _matched_keyword(event.summary, keywords)
        if matched is not None:
            return BlockedNight(night_date=night, blocking_event_summary=event.summary, matched_keyword=matched)
    return None
