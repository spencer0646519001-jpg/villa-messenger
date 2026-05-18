"""
Models for Google Calendar availability checking.

Decoupled from Google's API shape — PR8.5b will translate Google's raw API
response into CalendarEvent instances before calling the checker. This keeps
the checker pure and stub-friendly.
"""

from datetime import date

from pydantic import BaseModel, Field


class CalendarEvent(BaseModel):
    """A single event from a calendar, normalized for our purposes.

    Google's API returns events with timezone-aware datetimes or all-day
    date-only values. PR8.5b will normalize both into start_date / end_date
    here (end_date is EXCLUSIVE per Google's convention, meaning an event
    that 'occupies' 5/12 has start=5/12, end=5/13).
    """

    summary: str
    start_date: date
    end_date: date


class BlockedNight(BaseModel):
    """A single night marked as blocked + the event that blocked it."""

    night_date: date
    blocking_event_summary: str
    matched_keyword: str


class AvailabilityResult(BaseModel):
    has_any_blocked_nights: bool
    blocked_nights: list[BlockedNight] = Field(default_factory=list)
