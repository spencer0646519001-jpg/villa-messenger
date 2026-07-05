"""
Shared availability gate for all quote paths.

The gate turns the service-level availability outcome into the one question
quote flows need to answer: may we quote, should we block, or did calendar
verification fail and require a graceful fallback?
"""

from datetime import date
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.domain.availability_models import BlockedNight


AvailabilityGateStatus = Literal["available", "blocked", "error"]


class AvailabilityServiceLike(Protocol):
    def check(self, *, checkin_date: date, checkout_date: date) -> Any:
        """Return an object with status/result/error_reason attributes."""


class AvailabilityGateResult(BaseModel):
    can_quote: bool
    status: AvailabilityGateStatus
    blocked_nights: list[BlockedNight] = Field(default_factory=list)
    should_notify_owner: bool = False
    reason: str | None = None


def evaluate_availability_gate(
    *,
    availability_service: AvailabilityServiceLike | None,
    checkin: date,
    checkout: date,
) -> AvailabilityGateResult:
    if availability_service is None or not _service_enabled(availability_service):
        return _available()
    try:
        outcome = availability_service.check(checkin_date=checkin, checkout_date=checkout)
    except Exception as exc:  # noqa: BLE001 -- availability must not break replies
        return _error(f"availability check failed: {exc}")
    return _from_outcome(outcome)


def _service_enabled(availability_service: AvailabilityServiceLike) -> bool:
    return bool(getattr(availability_service, "enabled", True))


def _from_outcome(outcome: Any) -> AvailabilityGateResult:
    status = getattr(outcome, "status", None)
    if status == "blocked":
        return AvailabilityGateResult(
            can_quote=False,
            status="blocked",
            blocked_nights=_blocked_nights(outcome),
        )
    if status == "error":
        return _error(getattr(outcome, "error_reason", None))
    return _available()


def _blocked_nights(outcome: Any) -> list[BlockedNight]:
    result = getattr(outcome, "result", None)
    if result is None:
        return []
    return list(getattr(result, "blocked_nights", []) or [])


def _available() -> AvailabilityGateResult:
    return AvailabilityGateResult(can_quote=True, status="available")


def _error(reason: str | None) -> AvailabilityGateResult:
    return AvailabilityGateResult(
        can_quote=True,
        status="error",
        should_notify_owner=True,
        reason=reason or "availability check failed",
    )
