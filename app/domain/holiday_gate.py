"""Shared holiday-coverage gate for all quote paths.

The one question quote flows need answered before pricing: do we actually hold
the government calendar for every year this stay touches?

Why a gate and not a default: pricing_policy._resolve_price_type treats a date
it cannot find in special_dates as an ordinary day. That made "we have no data
for 2027" indistinguishable from "2027 has no holidays", and a customer asking
about 2027 春節 was quoted NT$29,000 against the owner's NT$60,000 -- an error
nobody could see, because the reply looked perfectly normal. Blocking the quote
and handing the stay to staff is the only safe way to be missing this data.

Mirrors evaluate_availability_gate's shape, and sits beside it in the same
place in the flow.
"""

from datetime import date, timedelta
from typing import Callable, Protocol

from pydantic import BaseModel, Field


class HolidayCoverage(Protocol):
    def __call__(self, tenant_id: int, years: list[int]) -> list[int]:
        """Make these years available if possible; return the ones still missing."""


class HolidayGateResult(BaseModel):
    can_quote: bool
    missing_years: list[int] = Field(default_factory=list)
    reason: str | None = None


def evaluate_holiday_gate(
    *,
    ensure_years: Callable[[int, list[int]], list[int]] | None,
    tenant_id: int,
    checkin: date,
    checkout: date,
) -> HolidayGateResult:
    """Block the quote unless every year the stay touches is covered.

    `ensure_years=None` means the caller has not wired holiday coverage at all
    (every construction that predates it, and the unit tests that build a bare
    service). Those keep today's behaviour: quote from whatever special_dates
    the tenant config carries.
    """
    if ensure_years is None:
        return HolidayGateResult(can_quote=True)
    years = _years_touched(checkin, checkout)
    try:
        missing = sorted(set(ensure_years(tenant_id, years)))
    except Exception as exc:  # noqa: BLE001
        # An unavailable calendar is exactly the case this gate exists for, so
        # a crash here blocks rather than falls through to weekday pricing.
        return HolidayGateResult(
            can_quote=False,
            missing_years=years,
            reason=f"holiday calendar lookup failed: {exc}",
        )
    if not missing:
        return HolidayGateResult(can_quote=True)
    return HolidayGateResult(
        can_quote=False,
        missing_years=missing,
        reason="no holiday calendar for " + ", ".join(str(year) for year in missing),
    )


def _years_touched(checkin: date, checkout: date) -> list[int]:
    """Every year needed to classify this stay's nights, neighbours included.

    Not just the years holding a priced night. Whether a night is a holiday
    depends on the whole run of non-working days it belongs to, and a 連假 can
    start in December and end in January -- so a stay over 2028-12-30/31 can
    only be classified once 2029 is on hand to say whether 1 January anchors
    that run. Without it those nights look like an unnamed weekend and price at
    the Saturday/weekday rate. Codex review of commit 0056e41 (P1).

    Hence one day either side of the priced nights. For any stay not sitting on
    a year boundary that is the same single year as before.

    A reversed range has nothing to price; pricing rejects it separately.
    """
    last_night = checkout - timedelta(days=1)
    if last_night < checkin:
        return []
    return sorted(
        {
            (checkin - timedelta(days=1)).year,
            checkin.year,
            last_night.year,
            (last_night + timedelta(days=1)).year,
        }
    )
