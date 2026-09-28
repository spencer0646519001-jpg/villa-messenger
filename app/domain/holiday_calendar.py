"""Turn a Taiwan government working-day calendar into pricing special_dates.

Pure functions, no I/O: this is the part of the holiday work the moat cares
about, so it stays testable in isolation and is pinned by a golden test
against the 2026 list the owner maintained by hand.

Why this module exists at all: `special_dates` in each tenant's config.json
was typed out a year at a time, and the 2027 entries were simply never added.
`pricing_policy._resolve_price_type` treats "not in the list" as "ordinary
day", so a customer asking about 2027 春節 was quoted the Saturday/weekday
rate -- NT$29,000 against the owner's own NT$60,000. Silently falling back to
the cheapest rate is the failure mode this whole change exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Sequence


_SPRING_FESTIVAL_MARKERS = ("春節", "農曆除夕", "除夕", "小年夜")
_SATURDAY = 5


@dataclass(frozen=True)
class HolidayDay:
    """One row of the source calendar: every day of the year, in order."""

    day: date
    is_holiday: bool
    description: str


def parse_calendar_payload(payload: Iterable[dict]) -> list[HolidayDay]:
    """Read the published JSON rows (date "YYYYMMDD", isHoliday, description).

    Rows that are missing or malformed are skipped rather than raising: a
    single bad row must not cost the whole year, and a year that ends up short
    is caught downstream by the coverage check, not here.

    Every field is type-checked rather than coerced, because coercion here
    produces exactly the failure this module exists to prevent -- a wrong
    price, quietly. Codex review of commit e97695e (P1): bool() on a missing
    isHoliday reads as a working day and cuts a 連假 run in half, while the
    string "false" reads as True. Either way the day count for the year is
    still 365, so the coverage check downstream waves it through and only the
    quote comes out wrong. A skipped row shortens the year instead, which that
    same check does catch. A missing description stays "" -- that genuinely
    means "not a named holiday" in this source -- but a non-string one is a
    malformed row, since silently blanking it would unname a holiday and
    downgrade its whole run.

    A date appearing twice drops out entirely, both copies. Codex review of
    commit 5f7b395 (P1): the source is one row per day, and a repeat breaks
    _consecutive_holiday_runs outright -- 2/27-3/1 with 2/28 duplicated splits
    into "2/27, 2/28" and "2/28, 3/1", and since only the first fragment holds
    the named day, 3/1 falls out of the holiday list and gets quoted at the
    weekday rate. Dropping the date leaves the year short, which the coverage
    check catches, instead of leaving a plausible-looking year that prices
    wrongly. That check must therefore count DISTINCT dates, not rows.
    """
    seen: dict[date, HolidayDay] = {}
    duplicated: set[date] = set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        raw_date = row.get("date")
        if not isinstance(raw_date, str):
            continue
        try:
            parsed = datetime.strptime(raw_date, "%Y%m%d").date()
        except ValueError:
            continue
        is_holiday = row.get("isHoliday")
        if not isinstance(is_holiday, bool):
            continue
        description = row.get("description", "")
        if not isinstance(description, str):
            continue
        if parsed in seen:
            duplicated.add(parsed)
            continue
        seen[parsed] = HolidayDay(
            day=parsed, is_holiday=is_holiday, description=description
        )
    for day in duplicated:
        seen.pop(day, None)
    return [seen[day] for day in sorted(seen)]


def derive_special_dates(days: Sequence[HolidayDay]) -> dict[str, list[str]]:
    """Build {"national_holidays": [...], "spring_festival": [...]} (ISO dates).

    The source marks EVERY non-working day with isHoliday=true, ordinary
    Saturdays and Sundays included, so that flag alone is not "national
    holiday". Only named days carry a description. The rule, verified day for
    day against the hand-written 2026 config:

      * take the isHoliday=true days and cut them into consecutive runs;
      * a run containing any named day is a national-holiday run, in full --
        this is what picks up the weekend either side of a 連假, exactly as the
        owner listed them;
      * a run whose named days mention 春節 / 除夕 / 小年夜 is also, in full, a
        spring-festival run;
      * a run of nothing but blank-description days is just a weekend.

    Spring-festival dates appear in BOTH lists, matching the existing config
    (春節 is legally a national holiday); pricing_policy resolves the overlap
    by priority.
    """
    national: list[date] = []
    spring: list[date] = []
    for run in _consecutive_holiday_runs(days):
        descriptions = [entry.description for entry in run if entry.description]
        if not descriptions:
            continue
        national.extend(entry.day for entry in run)
        if any(
            marker in description
            for description in descriptions
            for marker in _SPRING_FESTIVAL_MARKERS
        ):
            spring.extend(entry.day for entry in run)
    return {
        "national_holidays": [day.isoformat() for day in national],
        "spring_festival": [day.isoformat() for day in spring],
    }


def make_up_workdays(days: Sequence[HolidayDay]) -> list[date]:
    """Saturdays the government moved back to being working days (補班日).

    Recorded because the source gives it for free and it is the obvious next
    question about a holiday calendar. Nothing prices off it yet: 2026 has
    none, no real enquiry has ever turned on one, and a 補班 Saturday is still
    a Saturday to a guest booking a night away.
    """
    return [
        entry.day
        for entry in days
        if entry.day.weekday() == _SATURDAY and not entry.is_holiday
    ]


def _consecutive_holiday_runs(
    days: Sequence[HolidayDay],
) -> list[list[HolidayDay]]:
    runs: list[list[HolidayDay]] = []
    current: list[HolidayDay] = []
    for entry in sorted(days, key=lambda item: item.day):
        if not entry.is_holiday:
            continue
        if current and entry.day - current[-1].day == timedelta(days=1):
            current.append(entry)
            continue
        if current:
            runs.append(current)
        current = [entry]
    if current:
        runs.append(current)
    return runs
