"""
Tests for GoogleCalendarClient.

All Google API calls are mocked — these tests must not hit the network or
load real credentials. The googleapiclient.discovery.build call and the
service_account credentials loader are both patched.

Fixture event data uses real response shapes verified against the live
calendar: all-day events with start.date / end.date keys, end.date exclusive,
real titles like "枕123", "1房-枕123", "妃" (uncle's property — must not match).
"""

import inspect
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

import app.clients.google_calendar_client as client_module
from app.clients.google_calendar_client import (
    GoogleCalendarClient,
    GoogleCalendarError,
)
from app.domain.availability_models import CalendarEvent


# ---------- Helpers ----------


def _all_day_event(summary: str, start: str, end: str) -> dict:
    """Build a raw Google API event payload in all-day shape."""
    return {"summary": summary, "start": {"date": start}, "end": {"date": end}}


def _timed_event(summary: str, start_dt: str, end_dt: str) -> dict:
    """Build a raw Google API event payload in timed shape (dateTime keys)."""
    return {"summary": summary, "start": {"dateTime": start_dt}, "end": {"dateTime": end_dt}}


def _make_client_with_items(items: list[dict]) -> GoogleCalendarClient:
    """Build a client whose lazy service returns the given items list."""
    client = GoogleCalendarClient(
        credentials_path="dummy/path.json", calendar_id="cal-id"
    )
    fake_service = MagicMock()
    fake_service.events.return_value.list.return_value.execute.return_value = {
        "items": items
    }
    client._service = fake_service  # bypass _build_service entirely
    return client


def _make_client_with_exec_raises(exc: Exception) -> GoogleCalendarClient:
    client = GoogleCalendarClient(
        credentials_path="dummy/path.json", calendar_id="cal-id"
    )
    fake_service = MagicMock()
    fake_service.events.return_value.list.return_value.execute.side_effect = exc
    client._service = fake_service
    return client


# ============================================================
# CONSTRUCTION + LAZINESS
# ============================================================


def test_construction_does_not_touch_network_or_filesystem() -> None:
    # No exception even though the credentials path doesn't exist.
    client = GoogleCalendarClient(
        credentials_path="does/not/exist.json", calendar_id="cal-id"
    )
    assert client._service is None


def test_service_built_lazily_on_first_fetch() -> None:
    with patch.object(client_module, "build") as mock_build, patch.object(
        client_module.service_account.Credentials,
        "from_service_account_file",
    ) as mock_creds:
        fake_service = MagicMock()
        fake_service.events.return_value.list.return_value.execute.return_value = {
            "items": []
        }
        mock_build.return_value = fake_service

        client = GoogleCalendarClient(
            credentials_path="dummy/path.json", calendar_id="cal-id"
        )
        # not built yet
        mock_creds.assert_not_called()
        mock_build.assert_not_called()

        client.fetch_events(range_start=date(2026, 5, 12), range_end=date(2026, 5, 14))

        mock_creds.assert_called_once()
        mock_build.assert_called_once()


def test_service_reused_across_fetches() -> None:
    with patch.object(client_module, "build") as mock_build, patch.object(
        client_module.service_account.Credentials,
        "from_service_account_file",
    ):
        fake_service = MagicMock()
        fake_service.events.return_value.list.return_value.execute.return_value = {
            "items": []
        }
        mock_build.return_value = fake_service

        client = GoogleCalendarClient(
            credentials_path="dummy/path.json", calendar_id="cal-id"
        )
        client.fetch_events(range_start=date(2026, 5, 12), range_end=date(2026, 5, 14))
        client.fetch_events(range_start=date(2026, 5, 15), range_end=date(2026, 5, 17))

        assert mock_build.call_count == 1


# ============================================================
# NORMALIZATION: ALL-DAY EVENTS
# ============================================================


def test_all_day_event_normalizes_to_calendar_event() -> None:
    items = [_all_day_event("枕123", "2026-05-16", "2026-05-17")]
    client = _make_client_with_items(items)

    events = client.fetch_events(
        range_start=date(2026, 5, 16), range_end=date(2026, 5, 16)
    )

    assert len(events) == 1
    assert events[0] == CalendarEvent(
        summary="枕123",
        start_date=date(2026, 5, 16),
        end_date=date(2026, 5, 17),
    )


def test_all_day_event_end_date_exclusivity_preserved() -> None:
    # A multi-night booking: start=5/16, end=5/19 means nights 5/16, 5/17, 5/18.
    items = [_all_day_event("枕123（連住兩天）", "2026-05-16", "2026-05-19")]
    client = _make_client_with_items(items)

    events = client.fetch_events(
        range_start=date(2026, 5, 16), range_end=date(2026, 5, 18)
    )

    assert events[0].start_date == date(2026, 5, 16)
    assert events[0].end_date == date(2026, 5, 19)


def test_real_title_shapes_normalize_correctly() -> None:
    items = [
        _all_day_event("1房-枕123", "2026-05-12", "2026-05-13"),
        _all_day_event("2房-枕133", "2026-05-12", "2026-05-13"),
        _all_day_event("枕123、妃", "2026-05-14", "2026-05-15"),
        _all_day_event("妃", "2026-05-20", "2026-05-21"),
    ]
    client = _make_client_with_items(items)

    events = client.fetch_events(
        range_start=date(2026, 5, 12), range_end=date(2026, 5, 20)
    )

    assert [e.summary for e in events] == [
        "1房-枕123",
        "2房-枕133",
        "枕123、妃",
        "妃",
    ]


# ============================================================
# NORMALIZATION: TIMED EVENTS (FALLBACK)
# ============================================================


def test_timed_event_normalizes_dateTime_to_date_part() -> None:
    items = [
        _timed_event(
            "Timed booking",
            "2026-05-16T15:00:00+08:00",
            "2026-05-17T11:00:00+08:00",
        )
    ]
    client = _make_client_with_items(items)

    events = client.fetch_events(
        range_start=date(2026, 5, 16), range_end=date(2026, 5, 17)
    )

    assert events[0].start_date == date(2026, 5, 16)
    assert events[0].end_date == date(2026, 5, 17)


def test_mixed_all_day_and_timed_events_both_normalize() -> None:
    items = [
        _all_day_event("枕123", "2026-05-16", "2026-05-17"),
        _timed_event(
            "枕late", "2026-05-18T15:00:00+08:00", "2026-05-19T11:00:00+08:00"
        ),
    ]
    client = _make_client_with_items(items)

    events = client.fetch_events(
        range_start=date(2026, 5, 16), range_end=date(2026, 5, 19)
    )

    assert len(events) == 2
    assert events[0].start_date == date(2026, 5, 16)
    assert events[1].start_date == date(2026, 5, 18)


# ============================================================
# EDGE CASES
# ============================================================


def test_missing_summary_defaults_to_empty_string() -> None:
    items = [{"start": {"date": "2026-05-16"}, "end": {"date": "2026-05-17"}}]
    client = _make_client_with_items(items)

    events = client.fetch_events(
        range_start=date(2026, 5, 16), range_end=date(2026, 5, 16)
    )

    assert events[0].summary == ""


def test_null_summary_defaults_to_empty_string() -> None:
    items = [
        {
            "summary": None,
            "start": {"date": "2026-05-16"},
            "end": {"date": "2026-05-17"},
        }
    ]
    client = _make_client_with_items(items)

    events = client.fetch_events(
        range_start=date(2026, 5, 16), range_end=date(2026, 5, 16)
    )

    assert events[0].summary == ""


def test_empty_items_returns_empty_list() -> None:
    client = _make_client_with_items([])

    events = client.fetch_events(
        range_start=date(2026, 5, 12), range_end=date(2026, 5, 14)
    )

    assert events == []


def test_multiple_events_returned_in_order() -> None:
    items = [
        _all_day_event("枕123", "2026-05-12", "2026-05-13"),
        _all_day_event("妃", "2026-05-14", "2026-05-15"),
        _all_day_event("枕133", "2026-05-16", "2026-05-17"),
    ]
    client = _make_client_with_items(items)

    events = client.fetch_events(
        range_start=date(2026, 5, 12), range_end=date(2026, 5, 17)
    )

    assert len(events) == 3
    assert [e.summary for e in events] == ["枕123", "妃", "枕133"]


def test_endpoint_missing_date_and_dateTime_raises() -> None:
    items = [{"summary": "broken", "start": {}, "end": {"date": "2026-05-17"}}]
    client = _make_client_with_items(items)

    with pytest.raises(GoogleCalendarError, match="missing date/dateTime"):
        client.fetch_events(range_start=date(2026, 5, 16), range_end=date(2026, 5, 17))


# ============================================================
# API FAILURE WRAPPING
# ============================================================


def test_httperror_wrapped_as_google_calendar_error() -> None:
    resp = MagicMock()
    resp.status = 500
    resp.reason = "Internal Server Error"
    http_exc = HttpError(resp=resp, content=b"boom")

    client = _make_client_with_exec_raises(http_exc)

    with pytest.raises(GoogleCalendarError, match="calendar fetch failed"):
        client.fetch_events(range_start=date(2026, 5, 12), range_end=date(2026, 5, 14))


def test_oserror_during_fetch_wrapped() -> None:
    client = _make_client_with_exec_raises(OSError("network down"))

    with pytest.raises(GoogleCalendarError, match="calendar fetch failed"):
        client.fetch_events(range_start=date(2026, 5, 12), range_end=date(2026, 5, 14))


def test_underlying_exception_preserved_as_cause() -> None:
    original = OSError("network down")
    client = _make_client_with_exec_raises(original)

    with pytest.raises(GoogleCalendarError) as exc_info:
        client.fetch_events(range_start=date(2026, 5, 12), range_end=date(2026, 5, 14))

    assert exc_info.value.__cause__ is original


# ============================================================
# REQUEST PARAMETERS
# ============================================================


def test_fetch_passes_calendar_id_and_correct_params() -> None:
    client = GoogleCalendarClient(
        credentials_path="dummy/path.json", calendar_id="my-cal-id"
    )
    fake_service = MagicMock()
    fake_service.events.return_value.list.return_value.execute.return_value = {
        "items": []
    }
    client._service = fake_service

    client.fetch_events(range_start=date(2026, 5, 12), range_end=date(2026, 5, 14))

    list_kwargs = fake_service.events.return_value.list.call_args.kwargs
    assert list_kwargs["calendarId"] == "my-cal-id"
    assert list_kwargs["singleEvents"] is True
    assert list_kwargs["orderBy"] == "startTime"
    assert "2026-05-12" in list_kwargs["timeMin"]
    assert "2026-05-14" in list_kwargs["timeMax"]


# ============================================================
# METHOD-LENGTH DISCIPLINE
# ============================================================


def _body_line_count(func) -> int:
    src = inspect.getsource(func)
    lines = [line for line in src.splitlines()[1:] if line.strip() and not line.strip().startswith("#")]
    return len(lines)


@pytest.mark.parametrize(
    "func",
    [
        GoogleCalendarClient.__init__,
        GoogleCalendarClient.fetch_events,
        GoogleCalendarClient._list_events,
        GoogleCalendarClient._get_service,
        GoogleCalendarClient._build_service,
        GoogleCalendarClient._to_calendar_event,
        GoogleCalendarClient._extract_date,
    ],
)
def test_methods_under_15_body_lines(func) -> None:
    assert _body_line_count(func) <= 15, (
        f"{func.__qualname__} body too long: {_body_line_count(func)} lines"
    )
