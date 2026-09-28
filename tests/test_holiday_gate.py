from datetime import date

import pytest

from app.domain.holiday_gate import evaluate_holiday_gate


def _ensure(missing: list[int], calls: list | None = None):
    def ensure_years(tenant_id: int, years: list[int]) -> list[int]:
        if calls is not None:
            calls.append((tenant_id, years))
        return [year for year in missing if year in years]

    return ensure_years


def test_covered_years_may_quote() -> None:
    result = evaluate_holiday_gate(
        ensure_years=_ensure([]),
        tenant_id=1,
        checkin=date(2027, 2, 6),
        checkout=date(2027, 2, 8),
    )

    assert result.can_quote is True
    assert result.missing_years == []


def test_a_missing_year_blocks_the_quote() -> None:
    # The whole point: pricing treats an unknown date as an ordinary day, so
    # quoting without the calendar produced NT$29,000 for a 春節 stay the owner
    # prices at NT$60,000 -- and nothing in the reply looked wrong.
    result = evaluate_holiday_gate(
        ensure_years=_ensure([2027]),
        tenant_id=1,
        checkin=date(2027, 2, 6),
        checkout=date(2027, 2, 8),
    )

    assert result.can_quote is False
    assert result.missing_years == [2027]
    assert "2027" in result.reason


def test_unwired_coverage_keeps_existing_behaviour() -> None:
    # Every construction that predates this, and the unit tests that build a
    # bare service, pass nothing and must go on quoting from tenant config.
    result = evaluate_holiday_gate(
        ensure_years=None,
        tenant_id=1,
        checkin=date(2027, 2, 6),
        checkout=date(2027, 2, 8),
    )

    assert result.can_quote is True


def test_a_lookup_that_raises_blocks_rather_than_falls_through() -> None:
    def explode(tenant_id: int, years: list[int]) -> list[int]:
        raise RuntimeError("calendar service down")

    result = evaluate_holiday_gate(
        ensure_years=explode,
        tenant_id=1,
        checkin=date(2027, 2, 6),
        checkout=date(2027, 2, 8),
    )

    assert result.can_quote is False
    assert "calendar service down" in result.reason


@pytest.mark.parametrize(
    ("checkin", "checkout", "expected"),
    [
        # Nowhere near a year boundary: still just the one year.
        (date(2026, 5, 1), date(2026, 5, 3), [2026]),
        # Codex review of commit 0056e41 (P1): the priced nights here are
        # 12/30 and 12/31, but whether they are holidays depends on whether
        # 1 January anchors a 連假 -- so 2027 is needed to classify them, even
        # though no night of it is priced. Without it they read as an unnamed
        # weekend and price at the Saturday/weekday rate.
        (date(2026, 12, 30), date(2027, 1, 1), [2026, 2027]),
        (date(2026, 12, 31), date(2027, 1, 2), [2026, 2027]),
        # Arriving on New Year's Day: the run may have begun in December.
        (date(2027, 1, 1), date(2027, 1, 3), [2026, 2027]),
    ],
)
def test_the_years_needed_to_classify_the_nights_are_required(
    checkin: date, checkout: date, expected: list[int]
) -> None:
    calls: list = []
    evaluate_holiday_gate(
        ensure_years=_ensure([], calls), tenant_id=7, checkin=checkin, checkout=checkout
    )

    assert calls == [(7, expected)]


def test_a_reversed_range_asks_for_nothing() -> None:
    # Nothing to price, and pricing rejects the range on its own. Demanding a
    # calendar here would replace the existing "your dates look out of order"
    # question with an unrelated "staff will confirm".
    calls: list = []
    result = evaluate_holiday_gate(
        ensure_years=_ensure([], calls),
        tenant_id=1,
        checkin=date(2027, 5, 14),
        checkout=date(2027, 5, 12),
    )

    assert calls == [(1, [])]
    assert result.can_quote is True


def test_tenant_id_reaches_the_coverage_check() -> None:
    # Coverage is partly per-tenant: a year listed by hand in that tenant's
    # config.json counts as covered.
    calls: list = []
    evaluate_holiday_gate(
        ensure_years=_ensure([], calls),
        tenant_id=42,
        checkin=date(2026, 5, 1),
        checkout=date(2026, 5, 2),
    )

    assert calls[0][0] == 42
