"""
AvailabilityService — orchestrates calendar fetch + keyword-based blocking
check. Acts as the anti-corruption boundary between GoogleCalendarClient
(Google-typed errors) and InquiryService (Google-free).

Returns AvailabilityCheckOutcome with three states:
  - available: nothing blocks any night
  - blocked:   at least one night is blocked (outcome.result populated)
  - error:     calendar call failed (outcome.error_reason populated)

When enabled=False, check() returns "available" immediately without touching
the client. This is the gate for the tenant config v1_5_enabled flag and
lets tests/dev run with no credentials.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.clients.google_calendar_client import (
    GoogleCalendarClient,
    GoogleCalendarError,
)
from app.domain.availability_checker import check_availability
from app.domain.availability_models import AvailabilityResult


OutcomeStatus = Literal["available", "blocked", "error"]


class AvailabilityCheckOutcome(BaseModel):
    status: OutcomeStatus
    result: AvailabilityResult | None = None
    error_reason: str | None = None


class AvailabilityService:
    def __init__(
        self,
        *,
        client: GoogleCalendarClient,
        booking_keywords: list[str],
        enabled: bool,
    ) -> None:
        self._client = client
        self._booking_keywords = list(booking_keywords)
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, *, checkin_date: date, checkout_date: date) -> AvailabilityCheckOutcome:
        if not self._enabled:
            return AvailabilityCheckOutcome(status="available")
        try:
            events = self._client.fetch_events(
                range_start=checkin_date, range_end=checkout_date
            )
        except GoogleCalendarError as exc:
            return AvailabilityCheckOutcome(status="error", error_reason=str(exc))
        return self._build_outcome(events, checkin_date, checkout_date)

    def _build_outcome(
        self, events: list, checkin_date: date, checkout_date: date
    ) -> AvailabilityCheckOutcome:
        result = check_availability(
            events=events,
            checkin_date=checkin_date,
            checkout_date=checkout_date,
            booking_keywords=self._booking_keywords,
        )
        status: OutcomeStatus = "blocked" if result.has_any_blocked_nights else "available"
        return AvailabilityCheckOutcome(status=status, result=result)
