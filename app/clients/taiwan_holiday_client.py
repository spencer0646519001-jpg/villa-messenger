"""TaiwanHolidayClient: fetches one year of the government working-day calendar.

I/O boundary only -- it fetches and shape-checks, it does not interpret. The
pure derive_special_dates() in app/domain/holiday_calendar.py consumes its
output. Modelled on GoogleCalendarClient: nothing happens at construction, a
short timeout, and every underlying failure wrapped so callers never depend on
httpx's exception classes.

Source: the published JSON mirror of 行政院人事行政總處's 辦公日曆表, one file
per calendar year, each row {date: "YYYYMMDD", week, isHoliday, description}.

Deliberately uses module-level httpx.get rather than an httpx.Client instance:
tests/conftest.py patches httpx.get on the shared module object, so this call
is covered by the same outbound guard as everything else. Building a Client
here would slip straight past it -- that guard already had to be widened once
after a new httpx.get let real network calls into ~900 tests.
"""

import logging
from typing import Any, Callable

import httpx

from app.domain.holiday_calendar import HolidayDay, parse_calendar_payload


logger = logging.getLogger(__name__)

_DEFAULT_URL_TEMPLATE = (
    "https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json"
)
# Shorter than GoogleCalendarClient's 10s: this one can sit in the reply path
# on a cache miss, and a customer waiting on a LINE reply notices.
_DEFAULT_TIMEOUT_SECONDS = 5.0


class TaiwanHolidayError(Exception):
    """Raised when the calendar cannot be fetched or is not the expected shape."""


class TaiwanHolidayClient:
    def __init__(
        self,
        *,
        url_template: str = _DEFAULT_URL_TEMPLATE,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        http_get: Callable[..., Any] | None = None,
    ) -> None:
        self._url_template = url_template
        self._timeout_seconds = timeout_seconds
        # Injectable purely so tests can supply a fake without monkeypatching
        # the httpx module; production always takes the default.
        self._http_get = http_get

    def fetch_year(self, year: int) -> tuple[list[HolidayDay], list[dict]]:
        """Return (parsed days, the raw rows) so the caller can cache verbatim.

        Caching the raw rows rather than the parsed days lets the derivation
        rule change later without a refetch.
        """
        payload = self._fetch_payload(year)
        if not isinstance(payload, list):
            raise TaiwanHolidayError(
                f"holiday calendar for {year} is not a list of rows"
            )
        return parse_calendar_payload(payload), payload

    def _fetch_payload(self, year: int) -> Any:
        url = self._url_template.format(year=year)
        get = self._http_get if self._http_get is not None else httpx.get
        try:
            response = get(url, timeout=self._timeout_seconds)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 -- wrapped, never leaked upward
            raise TaiwanHolidayError(
                f"could not fetch holiday calendar for {year}: {exc}"
            ) from exc
