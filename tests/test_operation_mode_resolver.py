from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.domain.operation_mode_resolver import (
    OperationStateSnapshot,
    compute_most_recent_schedule_window,
    compute_most_recent_schedule_window_start,
    compute_next_schedule_boundary,
    resolve_effective_mode,
)


TPE = timezone(timedelta(hours=8))
TPE_ZONE = ZoneInfo("Asia/Taipei")


def _snapshot(
    *,
    auto_schedule_enabled: bool = True,
    start_time: time = time(23, 0),
    end_time: time = time(8, 0),
    manual_mode: str | None = None,
    manual_valid_until: datetime | None = None,
) -> OperationStateSnapshot:
    return OperationStateSnapshot(
        auto_schedule_enabled=auto_schedule_enabled,
        auto_on_start_time=start_time,
        auto_on_end_time=end_time,
        manual_mode=manual_mode,  # type: ignore[arg-type]
        manual_valid_until=manual_valid_until,
    )


def test_manual_on_override_with_future_validity_overrides_schedule() -> None:
    now = datetime(2026, 5, 12, 14, 0, tzinfo=TPE)
    state = _snapshot(
        manual_mode="on",
        manual_valid_until=datetime(2026, 5, 12, 23, 0, tzinfo=TPE),
    )

    assert resolve_effective_mode(state=state, now=now) == "on"


def test_manual_off_override_with_future_validity_overrides_schedule() -> None:
    now = datetime(2026, 5, 12, 23, 30, tzinfo=TPE)
    state = _snapshot(
        manual_mode="off",
        manual_valid_until=datetime(2026, 5, 13, 8, 0, tzinfo=TPE),
    )

    assert resolve_effective_mode(state=state, now=now) == "off"


def test_expired_manual_override_falls_through_to_schedule() -> None:
    now = datetime(2026, 5, 12, 23, 30, tzinfo=TPE)
    state = _snapshot(
        manual_mode="off",
        manual_valid_until=datetime(2026, 5, 12, 8, 0, tzinfo=TPE),
    )

    assert resolve_effective_mode(state=state, now=now) == "on"


def test_no_manual_override_schedule_disabled_returns_off() -> None:
    now = datetime(2026, 5, 12, 23, 30, tzinfo=TPE)
    state = _snapshot(auto_schedule_enabled=False)

    assert resolve_effective_mode(state=state, now=now) == "off"


def test_wrap_around_window_during_window_returns_on() -> None:
    now = datetime(2026, 5, 12, 2, 0, tzinfo=TPE)
    state = _snapshot(start_time=time(23, 0), end_time=time(8, 0))

    assert resolve_effective_mode(state=state, now=now) == "on"


def test_wrap_around_window_outside_window_returns_off() -> None:
    now = datetime(2026, 5, 12, 14, 0, tzinfo=TPE)
    state = _snapshot(start_time=time(23, 0), end_time=time(8, 0))

    assert resolve_effective_mode(state=state, now=now) == "off"


def test_non_wrap_window_inside_returns_on() -> None:
    now = datetime(2026, 5, 12, 12, 0, tzinfo=TPE)
    state = _snapshot(start_time=time(8, 0), end_time=time(18, 0))

    assert resolve_effective_mode(state=state, now=now) == "on"


def test_non_wrap_window_start_inclusive_end_exclusive() -> None:
    state = _snapshot(start_time=time(8, 0), end_time=time(18, 0))
    at_start = datetime(2026, 5, 12, 8, 0, tzinfo=TPE)
    at_end = datetime(2026, 5, 12, 18, 0, tzinfo=TPE)
    before_start = datetime(2026, 5, 12, 7, 59, tzinfo=TPE)

    assert resolve_effective_mode(state=state, now=at_start) == "on"
    assert resolve_effective_mode(state=state, now=at_end) == "off"
    assert resolve_effective_mode(state=state, now=before_start) == "off"


def test_resolver_wrap_schedule_at_exact_start_returns_on() -> None:
    now = datetime(2026, 5, 12, 23, 0, 0, tzinfo=TPE)
    state = _snapshot(start_time=time(23, 0), end_time=time(8, 0))

    assert resolve_effective_mode(state=state, now=now) == "on"


def test_resolver_wrap_schedule_at_exact_end_returns_off() -> None:
    now = datetime(2026, 5, 12, 8, 0, 0, tzinfo=TPE)
    state = _snapshot(start_time=time(23, 0), end_time=time(8, 0))

    assert resolve_effective_mode(state=state, now=now) == "off"


@pytest.mark.parametrize(
    "now,expected",
    [
        (
            datetime(2026, 5, 12, 14, 30, tzinfo=TPE),
            datetime(2026, 5, 12, 23, 0, tzinfo=TPE),
        ),
        (
            datetime(2026, 5, 12, 2, 0, tzinfo=TPE),
            datetime(2026, 5, 12, 8, 0, tzinfo=TPE),
        ),
        (
            datetime(2026, 5, 12, 23, 30, tzinfo=TPE),
            datetime(2026, 5, 13, 8, 0, tzinfo=TPE),
        ),
        (
            datetime(2026, 5, 12, 23, 0, tzinfo=TPE),
            datetime(2026, 5, 13, 8, 0, tzinfo=TPE),
        ),
        (
            datetime(2026, 5, 12, 8, 0, tzinfo=TPE),
            datetime(2026, 5, 12, 23, 0, tzinfo=TPE),
        ),
    ],
)
def test_compute_next_schedule_boundary_wrap_around(
    now: datetime, expected: datetime
) -> None:
    boundary = compute_next_schedule_boundary(
        start_time=time(23, 0),
        end_time=time(8, 0),
        now=now,
    )

    assert boundary == expected


@pytest.mark.parametrize(
    "now,expected",
    [
        (
            datetime(2026, 5, 12, 12, 0, tzinfo=TPE),
            datetime(2026, 5, 12, 18, 0, tzinfo=TPE),
        ),
        (
            datetime(2026, 5, 12, 22, 0, tzinfo=TPE),
            datetime(2026, 5, 13, 8, 0, tzinfo=TPE),
        ),
    ],
)
def test_compute_next_schedule_boundary_non_wrap(
    now: datetime, expected: datetime
) -> None:
    boundary = compute_next_schedule_boundary(
        start_time=time(8, 0),
        end_time=time(18, 0),
        now=now,
    )

    assert boundary == expected


def test_most_recent_schedule_window_start_daytime_returns_previous_night_start() -> None:
    start = compute_most_recent_schedule_window_start(
        start_time=time(23, 0),
        end_time=time(8, 0),
        now=datetime(2026, 3, 16, 10, 0, tzinfo=TPE_ZONE),
    )

    assert start == datetime(2026, 3, 15, 23, 0, tzinfo=TPE_ZONE)


def test_most_recent_schedule_window_start_early_morning_returns_prior_date_start() -> None:
    start = compute_most_recent_schedule_window_start(
        start_time=time(23, 0),
        end_time=time(8, 0),
        now=datetime(2026, 3, 16, 2, 0, tzinfo=TPE_ZONE),
    )

    assert start == datetime(2026, 3, 15, 23, 0, tzinfo=TPE_ZONE)


def test_most_recent_schedule_window_start_exact_boundaries() -> None:
    at_start = compute_most_recent_schedule_window_start(
        start_time=time(23, 0),
        end_time=time(8, 0),
        now=datetime(2026, 3, 16, 23, 0, tzinfo=TPE_ZONE),
    )
    at_end = compute_most_recent_schedule_window_start(
        start_time=time(23, 0),
        end_time=time(8, 0),
        now=datetime(2026, 3, 16, 8, 0, tzinfo=TPE_ZONE),
    )

    assert at_start == datetime(2026, 3, 16, 23, 0, tzinfo=TPE_ZONE)
    assert at_end == datetime(2026, 3, 15, 23, 0, tzinfo=TPE_ZONE)


def test_most_recent_schedule_window_start_preserves_asia_taipei_timezone() -> None:
    start = compute_most_recent_schedule_window_start(
        start_time=time(23, 0),
        end_time=time(8, 0),
        now=datetime(2026, 3, 16, 10, 0, tzinfo=TPE_ZONE),
    )

    assert getattr(start.tzinfo, "key", None) == "Asia/Taipei"
    assert start.utcoffset() == timedelta(hours=8)


def test_most_recent_schedule_window_daytime_ends_at_window_end() -> None:
    start, end = compute_most_recent_schedule_window(
        start_time=time(23, 0),
        end_time=time(8, 0),
        now=datetime(2026, 3, 16, 10, 0, tzinfo=TPE_ZONE),
    )

    assert start == datetime(2026, 3, 15, 23, 0, tzinfo=TPE_ZONE)
    assert end == datetime(2026, 3, 16, 8, 0, tzinfo=TPE_ZONE)


def test_most_recent_schedule_window_inside_window_ends_at_now() -> None:
    now = datetime(2026, 3, 16, 2, 0, tzinfo=TPE_ZONE)

    start, end = compute_most_recent_schedule_window(
        start_time=time(23, 0),
        end_time=time(8, 0),
        now=now,
    )

    assert start == datetime(2026, 3, 15, 23, 0, tzinfo=TPE_ZONE)
    assert end == now
