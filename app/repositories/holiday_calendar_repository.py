"""Local cache of the Taiwan government working-day calendar, one row per year.

Stores the payload exactly as fetched, not the derived holiday lists: the
derivation rule lives in app/domain/holiday_calendar.py and may be corrected
later, and re-deriving from a cached payload is free while refetching a year
from the network is not.

A cached year is what keeps quoting alive when the network is down. The one
thing this must never do is let a lookup failure read as "no holidays that
year" -- that is precisely the silent downgrade to weekday pricing this whole
change exists to remove -- so a missing or unreadable year returns None, and
callers are expected to treat None as "cannot price this stay".
"""

import json
import logging
from contextlib import closing
from pathlib import Path

from app.repositories._helpers import _utc_now_iso
from app.repositories.sqlite import get_connection


logger = logging.getLogger(__name__)


class HolidayCalendarRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def get_payload(self, year: int) -> list[dict] | None:
        """The cached rows for `year`, or None if absent or unreadable."""
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                "SELECT payload FROM holiday_calendar_cache WHERE year = ? LIMIT 1",
                (year,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            # Corrupt cache reads the same as no cache: the caller refetches,
            # and if that also fails the stay goes to staff rather than out at
            # the wrong price.
            logger.warning("holiday calendar cache for %s is not valid JSON", year)
            return None
        if not isinstance(payload, list):
            logger.warning("holiday calendar cache for %s is not a list", year)
            return None
        return payload

    def save_payload(self, year: int, payload: list[dict]) -> None:
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(
                """
                INSERT INTO holiday_calendar_cache (year, payload, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(year) DO UPDATE SET
                    payload = excluded.payload,
                    fetched_at = excluded.fetched_at
                """,
                (year, json.dumps(payload, ensure_ascii=False), _utc_now_iso()),
            )
            connection.commit()

    def cached_years(self) -> list[int]:
        with closing(get_connection(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT year FROM holiday_calendar_cache ORDER BY year"
            ).fetchall()
        return [row["year"] for row in rows]
