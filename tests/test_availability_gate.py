from datetime import date

from app.domain.availability_gate import evaluate_availability_gate
from app.domain.availability_models import AvailabilityResult, BlockedNight
from app.services.availability_service import AvailabilityCheckOutcome


class _FakeAvailabilityService:
    def __init__(
        self,
        *,
        outcome: AvailabilityCheckOutcome | None = None,
        enabled: bool = True,
        raises: Exception | None = None,
    ) -> None:
        self._outcome = outcome or AvailabilityCheckOutcome(status="available")
        self.enabled = enabled
        self._raises = raises
        self.calls: list[tuple[date, date]] = []

    def check(self, *, checkin_date: date, checkout_date: date) -> AvailabilityCheckOutcome:
        self.calls.append((checkin_date, checkout_date))
        if self._raises is not None:
            raise self._raises
        return self._outcome


def test_none_service_allows_quote() -> None:
    result = evaluate_availability_gate(
        availability_service=None,
        checkin=date(2026, 5, 12),
        checkout=date(2026, 5, 13),
    )

    assert result.can_quote is True
    assert result.status == "available"
    assert result.should_notify_owner is False


def test_disabled_service_allows_quote_without_calling_check() -> None:
    service = _FakeAvailabilityService(enabled=False)

    result = evaluate_availability_gate(
        availability_service=service,
        checkin=date(2026, 5, 12),
        checkout=date(2026, 5, 13),
    )

    assert result.status == "available"
    assert result.can_quote is True
    assert service.calls == []


def test_blocked_outcome_blocks_quote_and_carries_nights() -> None:
    blocked = BlockedNight(
        night_date=date(2026, 5, 12),
        blocking_event_summary="枕123",
        matched_keyword="枕",
    )
    service = _FakeAvailabilityService(
        outcome=AvailabilityCheckOutcome(
            status="blocked",
            result=AvailabilityResult(
                has_any_blocked_nights=True,
                blocked_nights=[blocked],
            ),
        )
    )

    result = evaluate_availability_gate(
        availability_service=service,
        checkin=date(2026, 5, 12),
        checkout=date(2026, 5, 13),
    )

    assert result.can_quote is False
    assert result.status == "blocked"
    assert result.blocked_nights == [blocked]


def test_error_outcome_allows_quote_and_notifies_owner() -> None:
    service = _FakeAvailabilityService(
        outcome=AvailabilityCheckOutcome(status="error", error_reason="network down")
    )

    result = evaluate_availability_gate(
        availability_service=service,
        checkin=date(2026, 5, 12),
        checkout=date(2026, 5, 13),
    )

    assert result.can_quote is True
    assert result.status == "error"
    assert result.should_notify_owner is True
    assert result.reason == "network down"


def test_unexpected_service_exception_becomes_error_gate_result() -> None:
    service = _FakeAvailabilityService(raises=RuntimeError("boom"))

    result = evaluate_availability_gate(
        availability_service=service,
        checkin=date(2026, 5, 12),
        checkout=date(2026, 5, 13),
    )

    assert result.can_quote is True
    assert result.status == "error"
    assert result.should_notify_owner is True
    assert "boom" in result.reason
