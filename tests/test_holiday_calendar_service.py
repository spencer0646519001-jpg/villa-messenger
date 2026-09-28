import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.clients.taiwan_holiday_client import TaiwanHolidayError
from app.domain.holiday_calendar import parse_calendar_payload
from app.services.holiday_calendar_service import HolidayCalendarService


_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_CONFIG = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "tenants"
    / "zhen123-house"
    / "config.json"
)


def _payload(year: int) -> list[dict]:
    return json.loads(
        (_FIXTURES / f"taiwan_calendar_{year}.json").read_text(encoding="utf-8")
    )


class _FakeRepo:
    def __init__(self, initial: dict[int, list[dict]] | None = None) -> None:
        self.rows: dict[int, list[dict]] = dict(initial or {})
        self.saves: list[int] = []

    def get_payload(self, year: int):
        return self.rows.get(year)

    def save_payload(self, year: int, payload: list[dict]) -> None:
        self.rows[year] = payload
        self.saves.append(year)

    def cached_years(self) -> list[int]:
        return sorted(self.rows)


class _FakeClient:
    def __init__(self, available: dict[int, list[dict]] | None = None) -> None:
        self.available = dict(available or {})
        self.calls: list[int] = []

    def fetch_year(self, year: int):
        self.calls.append(year)
        if year not in self.available:
            raise TaiwanHolidayError(f"no calendar for {year}")
        payload = self.available[year]
        return parse_calendar_payload(payload), payload


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def _service(*, repo=None, client=None, config=None, enabled=True, clock=None):
    return HolidayCalendarService(
        repository=repo or _FakeRepo(),
        client=client or _FakeClient(),
        tenant_config_special_dates_loader=(lambda tid: config or {}),
        enabled=enabled,
        now_provider=clock,
    )


# ============================================================
# COVERAGE
# ============================================================


def test_a_cached_year_needs_no_fetch() -> None:
    client = _FakeClient()
    service = _service(repo=_FakeRepo({2027: _payload(2027)}), client=client)

    assert service.ensure_years(1, [2027]) == []
    assert client.calls == []


def test_a_missing_year_is_fetched_and_cached() -> None:
    repo = _FakeRepo()
    client = _FakeClient({2027: _payload(2027)})
    service = _service(repo=repo, client=client)

    assert service.ensure_years(1, [2027]) == []
    assert client.calls == [2027]
    assert repo.saves == [2027]


def test_a_year_nobody_can_supply_is_reported_missing() -> None:
    # Reported, never silently treated as "that year has no holidays" -- the
    # gate turns this into a hand-off to staff.
    service = _service(client=_FakeClient({}))

    assert service.ensure_years(1, [2027]) == [2027]


def test_config_is_an_override_and_never_claims_coverage() -> None:
    # Codex review of commits 58da71f and 3fc5f83 (P1 twice). Letting config
    # vouch for a year meant one custom date marked the WHOLE year covered,
    # the government calendar was never fetched, and that year's 春節 priced
    # as an ordinary day -- this module's own bug, coming back through the
    # override path. Config is merged into the answer; it never answers
    # "is this year covered".
    service = _service(
        client=_FakeClient({}), config={"national_holidays": ["2027-12-31"]}
    )

    assert service.ensure_years(1, [2027]) == [2027]


def test_the_whole_config_year_still_does_not_vouch_for_itself() -> None:
    # Even the owner's complete, hand-written 2026 list. The cost of this is
    # that a cold cache plus a dead network sends 2026 stays to staff too --
    # the right direction to fail, and barely reachable: startup prewarms this
    # year and next, and a host that cannot reach the calendar cannot reach
    # LINE either.
    config = json.loads(_CONFIG.read_text(encoding="utf-8"))["special_dates"]
    service = _service(client=_FakeClient({}), config=config)

    assert service.ensure_years(1, [2026]) == [2026]


def test_a_fetched_year_is_covered_and_config_still_merges_in() -> None:
    config = json.loads(_CONFIG.read_text(encoding="utf-8"))["special_dates"]
    service = _service(client=_FakeClient({2027: _payload(2027)}), config=config)

    assert service.ensure_years(1, [2027]) == []
    special = service.special_dates_for(1)
    assert "2027-02-06" in special["spring_festival"]  # fetched
    assert "2026-02-17" in special["spring_festival"]  # config override


def test_prewarm_needs_no_tenant_and_caches_the_real_calendar() -> None:
    # Codex review of commit 58da71f (P2): startup called ensure_years(years)
    # after it had grown a tenant_id parameter, so the background task raised
    # TypeError every boot and nothing was ever prewarmed. Prewarming is
    # tenant-free by design -- it wants the real calendar cached whatever any
    # tenant config says about those years.
    repo = _FakeRepo()
    client = _FakeClient({2027: _payload(2027)})
    service = _service(repo=repo, client=client, config={"national_holidays": ["2027-01-01"]})

    assert service.prewarm([2027]) == []
    assert repo.saves == [2027]


def test_prewarm_reports_years_it_could_not_reach() -> None:
    service = _service(client=_FakeClient({}))

    assert service.prewarm([2027, 2028]) == [2027, 2028]


def test_a_failed_year_is_not_retried_on_every_message() -> None:
    # One dead network must not add a five-second timeout to every reply.
    client = _FakeClient({})
    service = _service(client=client)

    service.ensure_years(1, [2027])
    service.ensure_years(1, [2027])
    service.ensure_years(1, [2027])

    assert client.calls == [2027]


def test_a_later_success_clears_the_failure_memory() -> None:
    client = _FakeClient({2026: _payload(2026)})
    service = _service(client=client)
    assert service.ensure_years(1, [2027]) == [2027]

    # Any year fetching successfully is a cheap proxy for "the network is back".
    assert service.ensure_years(1, [2026]) == []

    service.ensure_years(1, [2027])
    assert client.calls.count(2027) == 2


def test_the_failure_memory_expires_so_a_blip_is_not_permanent() -> None:
    # Codex review of commit 78e3b97 (P1), and a direct consequence of sharing
    # one service across requests: a permanent memory meant a single transient
    # outage sent every enquiry for that year to staff until the process
    # restarted, even once the network was long back.
    clock = _Clock()
    client = _FakeClient({})
    service = _service(client=client, clock=clock)
    assert service.ensure_years(1, [2027]) == [2027]

    clock.advance(minutes=1)
    assert service.ensure_years(1, [2027]) == [2027]
    assert client.calls == [2027]  # still inside the cooldown

    clock.advance(minutes=5)
    client.available[2027] = _payload(2027)
    assert service.ensure_years(1, [2027]) == []
    assert client.calls == [2027, 2027]


def test_an_incomplete_year_is_rejected_rather_than_cached() -> None:
    # A short year means holidays are missing from it; caching it would bake
    # the wrong prices in.
    repo = _FakeRepo()
    client = _FakeClient({2027: _payload(2027)[:100]})
    service = _service(repo=repo, client=client)

    assert service.ensure_years(1, [2027]) == [2027]
    assert repo.saves == []


def test_a_cached_year_that_is_incomplete_is_refetched() -> None:
    repo = _FakeRepo({2027: _payload(2027)[:10]})
    client = _FakeClient({2027: _payload(2027)})
    service = _service(repo=repo, client=client)

    assert service.ensure_years(1, [2027]) == []
    assert client.calls == [2027]


def test_a_duplicated_day_makes_the_year_incomplete() -> None:
    # parse_calendar_payload drops both copies of a repeated date, so a
    # payload that repeats one day and omits another still has 365 rows.
    # Counting distinct dates is what catches it.
    rows = _payload(2027)
    rows[10] = dict(rows[11])
    service = _service(client=_FakeClient({2027: rows}))

    assert service.ensure_years(1, [2027]) == [2027]


def test_disabled_service_never_blocks_anything() -> None:
    client = _FakeClient({})
    service = _service(client=client, enabled=False)

    assert service.ensure_years(1, [2027]) == []
    assert client.calls == []


def test_years_are_deduplicated_and_sorted() -> None:
    service = _service(client=_FakeClient({}))

    assert service.ensure_years(1, [2028, 2027, 2027]) == [2027, 2028]


# ============================================================
# SPECIAL DATES FOR PRICING
# ============================================================


def test_a_new_year_run_is_not_cut_at_the_year_boundary() -> None:
    # Codex review of commit 3fc5f83 (P1): deriving each cached year on its
    # own split a 連假 that straddles new year. 2028-12-30/31 are an unnamed
    # weekend inside 2028's own payload, so they dropped out entirely, leaving
    # those two nights of a new-year stay at Saturday/weekday rates while
    # 2029-01-01 got the holiday price.
    y2028 = [
        {"date": "20281229", "isHoliday": False, "description": ""},
        {"date": "20281230", "isHoliday": True, "description": ""},
        {"date": "20281231", "isHoliday": True, "description": ""},
    ]
    y2029 = [
        {"date": "20290101", "isHoliday": True, "description": "開國紀念日"},
        {"date": "20290102", "isHoliday": False, "description": ""},
    ]
    service = _service(repo=_FakeRepo({2028: y2028, 2029: y2029}))

    national = service.special_dates_for(1)["national_holidays"]

    assert national == ["2028-12-30", "2028-12-31", "2029-01-01"]



def test_special_dates_come_from_the_cached_years() -> None:
    service = _service(repo=_FakeRepo({2027: _payload(2027)}))

    special = service.special_dates_for(1)

    assert "2027-02-06" in special["spring_festival"]
    assert "2027-02-06" in special["national_holidays"]


def test_config_entries_are_merged_in_and_survive() -> None:
    config = json.loads(_CONFIG.read_text(encoding="utf-8"))["special_dates"]
    service = _service(repo=_FakeRepo({2027: _payload(2027)}), config=config)

    special = service.special_dates_for(1)

    # 2026 from the hand-written config, 2027 from the fetched calendar.
    assert "2026-02-17" in special["spring_festival"]
    assert "2027-02-06" in special["spring_festival"]


def test_a_cached_year_reproduces_the_hand_written_config_exactly() -> None:
    # Restates the golden guarantee at the service level: switching 2026 over
    # to the fetched calendar cannot move a single date.
    config = json.loads(_CONFIG.read_text(encoding="utf-8"))["special_dates"]
    from_cache = _service(repo=_FakeRepo({2026: _payload(2026)}))
    from_config = _service(config=config)

    assert from_cache.special_dates_for(1) == from_config.special_dates_for(1)


def test_output_has_no_duplicates_when_both_sources_cover_a_year() -> None:
    config = json.loads(_CONFIG.read_text(encoding="utf-8"))["special_dates"]
    service = _service(repo=_FakeRepo({2026: _payload(2026)}), config=config)

    special = service.special_dates_for(1)

    assert len(special["national_holidays"]) == len(set(special["national_holidays"]))
    assert len(special["spring_festival"]) == 9


def test_disabled_service_still_serves_the_config(monkeypatch) -> None:
    config = json.loads(_CONFIG.read_text(encoding="utf-8"))["special_dates"]
    service = _service(repo=_FakeRepo({2027: _payload(2027)}), config=config, enabled=False)

    special = service.special_dates_for(1)

    assert "2026-02-17" in special["spring_festival"]
    assert "2027-02-06" not in special["spring_festival"]


@pytest.mark.parametrize("config", [None, {}, {"national_holidays": None}])
def test_an_absent_config_block_is_tolerated(config) -> None:
    service = _service(repo=_FakeRepo({2027: _payload(2027)}), config=config)

    assert "2027-02-06" in service.special_dates_for(1)["spring_festival"]
