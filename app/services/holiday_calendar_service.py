"""Keeps the Taiwan holiday calendar available to pricing, one year at a time.

Anti-corruption boundary between TaiwanHolidayClient (network), the SQLite
cache, and the pure derivation in app/domain/holiday_calendar.py. Pricing never
learns any of that exists -- it still receives the same
{"national_holidays": [...], "spring_festival": [...]} dict it always has.

Order for each year: cache, then network, then give up. Giving up means saying
so out loud (ensure_years reports the year as missing and the gate blocks the
quote), never returning an empty holiday list, because "no data" priced as "no
holidays" is the bug this replaces.

The tenant's own config.json special_dates is merged into the answer as an
override, which is what keeps 2026 pricing from shifting underneath the owner.
It is NOT evidence that a year is covered -- see ensure_years for why letting it
vouch for coverage put this module's own bug back through the override path.
"""

import calendar
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.clients.taiwan_holiday_client import TaiwanHolidayError
from app.domain.holiday_calendar import derive_special_dates, parse_calendar_payload


logger = logging.getLogger(__name__)

# How long a failed year is left alone before trying again. Long enough that a
# sustained outage costs one timeout per year per five minutes rather than one
# per message, short enough that quoting resumes on its own once the network is
# back. It has to expire: the service is shared across requests now, so a
# permanent memory would send every enquiry for that year to staff until the
# process restarted -- Codex review of commit 78e3b97 (P1), which was a
# consequence of sharing the instance in the first place.
_FAILURE_BACKOFF = timedelta(minutes=5)


class HolidayCalendarService:
    def __init__(
        self,
        *,
        repository,
        client,
        tenant_config_special_dates_loader: Callable[[int], dict] | None = None,
        enabled: bool = True,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._client = client
        self._config_loader = tenant_config_special_dates_loader or (lambda tid: {})
        self.enabled = enabled
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._failed_at: dict[int, datetime] = {}

    # ---- coverage -------------------------------------------------------

    def ensure_years(self, tenant_id: int, years: list[int]) -> list[int]:
        """Make each year available, and return the ones that could not be.

        Cached years cost nothing; an absent year is fetched once. A failed
        fetch is left alone for _FAILURE_BACKOFF so one dead network does not
        add a five-second timeout to every subsequent reply, then retried; a
        success on any year clears every failure, a cheap proxy for "the
        network is back".

        The tenant's config.json special_dates does NOT count as coverage. It
        is an override that gets merged into the result, and nothing about it
        proves a year is completely described: a tenant who added one custom
        date for some year would otherwise have the whole year marked covered,
        the government calendar never fetched, and that year's 春節 priced as
        an ordinary day -- the exact bug this module removes, coming back
        through the override path (Codex review of commits 58da71f and
        3fc5f83, P1 twice).

        The cost is that a year is missing until it has been fetched at least
        once, so a cold cache plus a dead network sends those stays to staff.
        That is the right direction to fail, and barely reachable in practice:
        startup prewarms this year and next, and a host that cannot reach the
        calendar cannot reach LINE either, so the bot is not replying anyway.

        `tenant_id` stays in the signature because coverage is asked per
        enquiry and a future tenant-scoped rule belongs here.
        """
        return self._missing_years(years)

    def prewarm(self, years: list[int]) -> list[int]:
        """Cache these years before any customer asks; return what is missing.

        Startup-only, and tenant-free on purpose: prewarming wants the real
        government calendar in the cache, whatever any tenant's config happens
        to say about those years. Same work as ensure_years today, because
        config stopped counting as coverage; kept separate because the two
        callers are asking different questions and only one of them has a
        tenant to ask about.
        """
        return self._missing_years(years)

    def _missing_years(self, years: list[int]) -> list[int]:
        if not self.enabled:
            return []
        return [year for year in sorted(set(years)) if not self._have_year(year)]

    def _have_year(self, year: int) -> bool:
        if self._year_is_complete(self._repository.get_payload(year), year):
            return True
        if self._in_backoff(year):
            return False
        return self._fetch_and_cache(year)

    def _in_backoff(self, year: int) -> bool:
        failed_at = self._failed_at.get(year)
        if failed_at is None:
            return False
        return self._now() - failed_at < _FAILURE_BACKOFF

    def _fetch_and_cache(self, year: int) -> bool:
        try:
            _, payload = self._client.fetch_year(year)
        except TaiwanHolidayError:
            logger.warning("holiday calendar fetch failed for %s", year, exc_info=True)
            self._failed_at[year] = self._now()
            return False
        except Exception:  # noqa: BLE001 -- a bad calendar must not break replies
            logger.warning(
                "holiday calendar fetch raised unexpectedly for %s", year, exc_info=True
            )
            self._failed_at[year] = self._now()
            return False
        if not self._year_is_complete(payload, year):
            logger.warning("holiday calendar for %s is incomplete; not caching", year)
            self._failed_at[year] = self._now()
            return False
        self._repository.save_payload(year, payload)
        self._failed_at.clear()
        return True

    @staticmethod
    def _year_is_complete(payload, year: int) -> bool:
        """Every day of `year` present exactly once.

        Counts DISTINCT dates inside the year, not rows: parse_calendar_payload
        drops both copies of a duplicated date, so a payload that repeated one
        day and omitted another would still have 365 rows while silently
        missing a holiday.
        """
        if not payload:
            return False
        days = {
            entry.day
            for entry in parse_calendar_payload(payload)
            if entry.day.year == year
        }
        return len(days) == 366 if calendar.isleap(year) else len(days) == 365

    # ---- special_dates for pricing --------------------------------------

    def special_dates_for(self, tenant_id: int) -> dict:
        """Config overrides merged over every year currently cached."""
        merged: dict[str, list[str]] = {
            "national_holidays": [],
            "spring_festival": [],
        }
        if self.enabled:
            # Every cached year is parsed into ONE list before deriving, so a
            # 連假 that straddles new year stays a single run. Deriving year by
            # year cut it at the boundary: 2028-12-30/31 are an unnamed weekend
            # inside 2028's own payload and were dropped, leaving those two
            # nights of a new-year stay at Saturday/weekday rates while
            # 2029-01-01 got the holiday price. Codex review of commit 3fc5f83
            # (P1).
            days = []
            for year in self._repository.cached_years():
                payload = self._repository.get_payload(year)
                if payload is not None:
                    days.extend(parse_calendar_payload(payload))
            derived = derive_special_dates(days)
            for key in merged:
                merged[key].extend(derived[key])
        config = self._config_loader(tenant_id) or {}
        for key in merged:
            merged[key].extend(
                value for value in (config.get(key) or []) if isinstance(value, str)
            )
        return {key: sorted(set(values)) for key, values in merged.items()}
