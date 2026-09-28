import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Iterator

import pytest

from app.repositories.holiday_calendar_repository import HolidayCalendarRepository
from app.repositories.sqlite import get_connection, init_db


@pytest.fixture
def database_path() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-holiday-cache-repo")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "holiday-cache-tests.db"
    try:
        init_db(path)
        yield path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


_PAYLOAD = [
    {"date": "20270101", "week": "五", "isHoliday": True, "description": "開國紀念日"},
    {"date": "20270102", "week": "六", "isHoliday": True, "description": ""},
]


def test_init_db_creates_the_table(database_path: Path) -> None:
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='holiday_calendar_cache'"
        ).fetchone()

    assert row is not None


def test_missing_year_reads_as_none_not_as_an_empty_year(database_path: Path) -> None:
    # The distinction that matters: "no data" must never be mistaken for "no
    # holidays", which is what silently downgrades a 春節 stay to weekday rates.
    repo = HolidayCalendarRepository(database_path)

    assert repo.get_payload(2027) is None


def test_save_then_get_round_trips_the_payload(database_path: Path) -> None:
    repo = HolidayCalendarRepository(database_path)

    repo.save_payload(2027, _PAYLOAD)

    assert repo.get_payload(2027) == _PAYLOAD


def test_saving_the_same_year_twice_replaces_it(database_path: Path) -> None:
    repo = HolidayCalendarRepository(database_path)
    repo.save_payload(2027, _PAYLOAD)

    repo.save_payload(2027, [{"date": "20270101", "isHoliday": False, "description": ""}])

    payload = repo.get_payload(2027)
    assert payload is not None
    assert len(payload) == 1
    assert repo.cached_years() == [2027]


def test_non_ascii_descriptions_survive_the_round_trip(database_path: Path) -> None:
    repo = HolidayCalendarRepository(database_path)

    repo.save_payload(2027, [{"date": "20270206", "isHoliday": True, "description": "春節"}])

    payload = repo.get_payload(2027)
    assert payload is not None
    assert payload[0]["description"] == "春節"


def test_cached_years_are_sorted(database_path: Path) -> None:
    repo = HolidayCalendarRepository(database_path)
    repo.save_payload(2028, _PAYLOAD)
    repo.save_payload(2026, _PAYLOAD)
    repo.save_payload(2027, _PAYLOAD)

    assert repo.cached_years() == [2026, 2027, 2028]


@pytest.mark.parametrize("stored", ["not json at all", '{"not": "a list"}'])
def test_corrupt_cache_reads_as_missing(database_path: Path, stored: str) -> None:
    # Reading corrupt cache as None sends the caller to a refetch, and failing
    # that to staff -- never to a quote built on nothing.
    repo = HolidayCalendarRepository(database_path)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "INSERT INTO holiday_calendar_cache (year, payload, fetched_at) VALUES (?, ?, ?)",
            (2027, stored, "2026-09-23T00:00:00+00:00"),
        )
        connection.commit()

    assert repo.get_payload(2027) is None


def test_fetched_at_is_recorded(database_path: Path) -> None:
    repo = HolidayCalendarRepository(database_path)

    repo.save_payload(2027, _PAYLOAD)

    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT fetched_at FROM holiday_calendar_cache WHERE year = 2027"
        ).fetchone()
    assert row["fetched_at"]


def test_year_is_the_primary_key(database_path: Path) -> None:
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "INSERT INTO holiday_calendar_cache (year, payload, fetched_at) VALUES (2027, '[]', 'x')"
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO holiday_calendar_cache (year, payload, fetched_at) VALUES (2027, '[]', 'y')"
            )
