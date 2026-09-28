import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.clients.taiwan_holiday_client import (
    TaiwanHolidayClient,
    TaiwanHolidayError,
)


_FIXTURES = Path(__file__).resolve().parent / "fixtures"


class _FakeResponse:
    def __init__(self, payload=None, *, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _fake_get(response, calls: list | None = None):
    def get(url, **kwargs):
        if calls is not None:
            calls.append((url, kwargs))
        return response

    return get


def _real_payload(year: int):
    return json.loads(
        (_FIXTURES / f"taiwan_calendar_{year}.json").read_text(encoding="utf-8")
    )


def test_constructing_the_client_touches_nothing() -> None:
    # Same contract as GoogleCalendarClient: building one must be free, so
    # wiring it into every request is not a per-request network cost.
    TaiwanHolidayClient(http_get=_fake_get(_FakeResponse([])))


def test_fetch_year_returns_parsed_days_and_the_raw_rows() -> None:
    payload = _real_payload(2027)
    client = TaiwanHolidayClient(http_get=_fake_get(_FakeResponse(payload)))

    days, raw = client.fetch_year(2027)

    assert len(days) == 365
    assert days[0].day == date(2027, 1, 1)
    # Raw rows come back so the caller can cache verbatim and re-derive later
    # if the derivation rule changes.
    assert raw == payload


def test_the_year_goes_into_the_url() -> None:
    calls: list = []
    client = TaiwanHolidayClient(http_get=_fake_get(_FakeResponse([]), calls))

    try:
        client.fetch_year(2029)
    except TaiwanHolidayError:
        pass

    assert calls[0][0].endswith("/2029.json")


def test_timeout_is_passed_and_shorter_than_the_calendar_client() -> None:
    # This one can sit in the reply path on a cache miss, where a customer is
    # waiting; GoogleCalendarClient's 10s would be too long here.
    calls: list = []
    client = TaiwanHolidayClient(http_get=_fake_get(_FakeResponse(_real_payload(2027)), calls))

    client.fetch_year(2027)

    assert calls[0][1]["timeout"] == 5.0


def test_a_custom_url_template_is_honoured() -> None:
    calls: list = []
    client = TaiwanHolidayClient(
        url_template="https://example.test/cal/{year}",
        http_get=_fake_get(_FakeResponse([]), calls),
    )

    try:
        client.fetch_year(2027)
    except TaiwanHolidayError:
        pass

    assert calls[0][0] == "https://example.test/cal/2027"


@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(error=httpx.HTTPError("boom")),
        _FakeResponse(error=httpx.ConnectTimeout("slow")),
        _FakeResponse(payload=ValueError("not json")),
    ],
)
def test_every_transport_failure_becomes_a_taiwan_holiday_error(response) -> None:
    # Callers must never have to know this module speaks httpx.
    client = TaiwanHolidayClient(http_get=_fake_get(response))

    with pytest.raises(TaiwanHolidayError):
        client.fetch_year(2027)


def test_a_payload_that_is_not_a_list_is_rejected() -> None:
    client = TaiwanHolidayClient(http_get=_fake_get(_FakeResponse({"oops": True})))

    with pytest.raises(TaiwanHolidayError):
        client.fetch_year(2027)


def test_the_error_names_the_year() -> None:
    client = TaiwanHolidayClient(http_get=_fake_get(_FakeResponse(error=httpx.HTTPError("x"))))

    with pytest.raises(TaiwanHolidayError, match="2031"):
        client.fetch_year(2031)


def test_default_client_uses_module_level_httpx_get(monkeypatch) -> None:
    # Load-bearing: tests/conftest.py guards outbound HTTP by patching
    # httpx.get on the shared module object. An httpx.Client instance here
    # would slip straight past that guard -- which already had to be widened
    # once after a new httpx.get let real network calls into ~900 tests.
    seen: list = []

    def fake_get(url, **kwargs):
        seen.append(url)
        return _FakeResponse([])

    monkeypatch.setattr(httpx, "get", fake_get)

    try:
        TaiwanHolidayClient().fetch_year(2027)
    except TaiwanHolidayError:
        pass

    assert seen and seen[0].endswith("/2027.json")
