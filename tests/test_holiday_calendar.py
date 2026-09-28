import json
from datetime import date
from pathlib import Path

import pytest

from app.domain.holiday_calendar import (
    HolidayDay,
    derive_special_dates,
    make_up_workdays,
    parse_calendar_payload,
)


_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_CONFIG = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "tenants"
    / "zhen123-house"
    / "config.json"
)


def _calendar(year: int):
    payload = json.loads(
        (_FIXTURES / f"taiwan_calendar_{year}.json").read_text(encoding="utf-8")
    )
    return parse_calendar_payload(payload)


def _day(iso: str, *, holiday: bool, description: str = "") -> HolidayDay:
    return HolidayDay(
        day=date.fromisoformat(iso), is_holiday=holiday, description=description
    )


# ============================================================
# GOLDEN REGRESSION
# ============================================================


def test_derived_2026_matches_the_hand_written_config_day_for_day() -> None:
    """The one test that makes switching to a fetched calendar safe.

    zhen123-house's special_dates for 2026 were typed out by hand from the
    government working-day calendar. If deriving them from the published
    calendar reproduces that list exactly, then the fetch cannot change any
    2026 quote -- which is what lets this land without re-checking a year of
    pricing behaviour by hand.
    """
    config = json.loads(_CONFIG.read_text(encoding="utf-8"))["special_dates"]

    derived = derive_special_dates(_calendar(2026))

    assert derived["national_holidays"] == sorted(config["national_holidays"])
    assert derived["spring_festival"] == sorted(config["spring_festival"])
    assert len(derived["national_holidays"]) == 36
    assert len(derived["spring_festival"]) == 9


def test_2027_spring_festival_covers_the_mispriced_stay() -> None:
    # The enquiry that started all of this: 2027/02/06-02/08, quoted at
    # Saturday + weekday rates because config.json had no 2027 entries at all.
    derived = derive_special_dates(_calendar(2027))

    assert derived["spring_festival"] == [
        "2027-02-04",  # 小年夜
        "2027-02-05",  # 農曆除夕
        "2027-02-06",
        "2027-02-07",
        "2027-02-08",
        "2027-02-09",  # 補假
        "2027-02-10",  # 補假
    ]
    # 春節 is a national holiday too, so it belongs in both lists -- same shape
    # the hand-written config used.
    for iso in derived["spring_festival"]:
        assert iso in derived["national_holidays"]


# ============================================================
# THE RUN RULE
# ============================================================


def test_plain_weekend_runs_are_not_national_holidays() -> None:
    # Every non-working day carries isHoliday=true in the source, ordinary
    # weekends included. Only a named day makes a run a holiday.
    days = [
        _day("2026-03-06", holiday=False),
        _day("2026-03-07", holiday=True),
        _day("2026-03-08", holiday=True),
        _day("2026-03-09", holiday=False),
    ]

    assert derive_special_dates(days) == {
        "national_holidays": [],
        "spring_festival": [],
    }


def test_one_named_day_pulls_in_the_whole_run() -> None:
    # This is how the weekend either side of a 連假 gets the holiday rate,
    # matching how the owner listed them by hand.
    days = [
        _day("2026-02-27", holiday=True, description="和平紀念日"),
        _day("2026-02-28", holiday=True),
        _day("2026-03-01", holiday=True),
        _day("2026-03-02", holiday=False),
    ]

    assert derive_special_dates(days)["national_holidays"] == [
        "2026-02-27",
        "2026-02-28",
        "2026-03-01",
    ]


@pytest.mark.parametrize("marker", ["春節", "農曆除夕", "小年夜"])
def test_any_spring_festival_marker_makes_the_run_spring_festival(marker: str) -> None:
    days = [
        _day("2027-02-04", holiday=True),
        _day("2027-02-05", holiday=True, description=marker),
        _day("2027-02-06", holiday=True),
        _day("2027-02-07", holiday=False),
    ]

    derived = derive_special_dates(days)

    assert derived["spring_festival"] == ["2027-02-04", "2027-02-05", "2027-02-06"]
    assert derived["national_holidays"] == derived["spring_festival"]


def test_runs_are_cut_by_a_working_day_between_them() -> None:
    days = [
        _day("2026-01-01", holiday=True, description="開國紀念日"),
        _day("2026-01-02", holiday=False),
        _day("2026-01-03", holiday=True),
        _day("2026-01-04", holiday=True),
    ]

    # 1/1 stands alone; the 1/3-1/4 weekend is unnamed and stays ordinary.
    assert derive_special_dates(days)["national_holidays"] == ["2026-01-01"]


def test_unsorted_input_is_handled() -> None:
    days = [
        _day("2026-03-01", holiday=True),
        _day("2026-02-27", holiday=True, description="和平紀念日"),
        _day("2026-02-28", holiday=True),
    ]

    assert len(derive_special_dates(days)["national_holidays"]) == 3


# ============================================================
# PAYLOAD PARSING
# ============================================================


def test_parse_reads_every_day_of_the_year() -> None:
    days = _calendar(2026)

    assert len(days) == 365
    assert days[0].day == date(2026, 1, 1)
    assert days[0].description == "開國紀念日"
    assert days[-1].day == date(2026, 12, 31)


def test_parse_skips_malformed_rows_without_losing_the_year() -> None:
    payload = [
        {"date": "20260101", "isHoliday": True, "description": "開國紀念日"},
        {"date": "not-a-date", "isHoliday": True},
        {"isHoliday": True},
        "not even a row",
        {"date": "20260102", "isHoliday": False},
    ]

    days = parse_calendar_payload(payload)

    assert [entry.day for entry in days] == [date(2026, 1, 1), date(2026, 1, 2)]


@pytest.mark.parametrize(
    "row",
    [
        # Codex review of commit e97695e (P1). bool() would read the first as
        # a working day -- cutting a 連假 run in half and downgrading the days
        # inside it -- and the second as a holiday. The year still has 365
        # rows either way, so nothing downstream would notice; dropping the
        # row shortens the year, which the coverage check does catch.
        {"date": "20270206", "description": "春節"},
        {"date": "20270206", "isHoliday": "false", "description": ""},
        {"date": "20270206", "isHoliday": 1, "description": ""},
        {"date": "20270206", "isHoliday": None, "description": ""},
        # A non-string description would be blanked, unnaming a holiday and
        # taking its whole run down with it.
        {"date": "20270206", "isHoliday": True, "description": 123},
    ],
)
def test_parse_rejects_rows_whose_flags_are_not_the_right_type(row: dict) -> None:
    assert parse_calendar_payload([row]) == []


def test_parse_drops_both_copies_of_a_duplicated_date() -> None:
    # Codex review of commit 5f7b395 (P1). The source is one row per day, and
    # a repeat splits _consecutive_holiday_runs in two: 2/27-3/1 with 2/28
    # duplicated became "2/27, 2/28" plus "2/28, 3/1", and only the first
    # fragment held the named day, so 3/1 dropped out of the holiday list and
    # would have been quoted at the weekday rate. Removing the date leaves the
    # year short, which the coverage check catches.
    payload = [
        {"date": "20260227", "isHoliday": True, "description": "和平紀念日"},
        {"date": "20260228", "isHoliday": True, "description": ""},
        {"date": "20260228", "isHoliday": True, "description": ""},
        {"date": "20260301", "isHoliday": True, "description": ""},
    ]

    days = parse_calendar_payload(payload)

    assert [entry.day for entry in days] == [date(2026, 2, 27), date(2026, 3, 1)]


def test_parse_defaults_a_missing_description_to_empty() -> None:
    # Absent genuinely means "not a named holiday" in this source, unlike a
    # description of the wrong type.
    days = parse_calendar_payload([{"date": "20260103", "isHoliday": True}])

    assert days[0].description == ""


def test_empty_input_derives_empty_lists() -> None:
    assert derive_special_dates([]) == {
        "national_holidays": [],
        "spring_festival": [],
    }


# ============================================================
# MAKE-UP WORKDAYS (recorded, not yet priced)
# ============================================================


def test_make_up_workdays_finds_a_working_saturday() -> None:
    days = [
        _day("2026-03-07", holiday=False),  # Saturday, made a working day
        _day("2026-03-08", holiday=True),  # Sunday
        _day("2026-03-09", holiday=False),  # Monday, an ordinary working day
    ]

    assert make_up_workdays(days) == [date(2026, 3, 7)]


@pytest.mark.parametrize("year", [2026, 2027])
def test_neither_published_year_has_a_make_up_workday(year: int) -> None:
    # Documents why nothing prices off 補班日 yet: the real calendars this
    # change ships against contain none.
    assert make_up_workdays(_calendar(year)) == []
