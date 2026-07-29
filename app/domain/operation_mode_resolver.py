from datetime import datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel


Mode = Literal["on", "off"]


class OperationStateSnapshot(BaseModel):
    auto_schedule_enabled: bool
    auto_on_start_time: time
    auto_on_end_time: time
    manual_mode: Mode | None = None
    manual_valid_until: datetime | None = None


def resolve_effective_mode(
    *,
    state: OperationStateSnapshot,
    now: datetime,
) -> Mode:
    if (
        state.manual_mode is not None
        and state.manual_valid_until is not None
        and now < state.manual_valid_until
    ):
        return state.manual_mode

    if not state.auto_schedule_enabled:
        return "off"

    return _schedule_mode_at(
        start=state.auto_on_start_time,
        end=state.auto_on_end_time,
        now_time=now.time(),
    )


def compute_next_schedule_boundary(
    *,
    start_time: time,
    end_time: time,
    now: datetime,
) -> datetime:
    today_start = _combine(now, start_time)
    today_end = _combine(now, end_time)
    tomorrow_start = today_start + timedelta(days=1)
    tomorrow_end = today_end + timedelta(days=1)

    candidates = sorted([today_start, today_end, tomorrow_start, tomorrow_end])
    for candidate in candidates:
        if candidate > now:
            return candidate
    # Unreachable: tomorrow boundaries are always > now.
    return tomorrow_start


def compute_next_active_window_end(
    *,
    start_time: time,
    end_time: time,
    now: datetime,
) -> datetime:
    """Next moment the auto-on window closes (an on->off transition).

    Unlike compute_next_schedule_boundary (which stops at whichever boundary
    comes first, start or end), this always resolves to an end_time
    occurrence -- so a caller pausing before or during the upcoming on-window
    gets covered through its entire remaining span, not just until the
    window starts.
    """
    today_end = _combine(now, end_time)
    return today_end if today_end > now else today_end + timedelta(days=1)


def compute_most_recent_schedule_window_start(
    *,
    start_time: time,
    end_time: time,
    now: datetime,
) -> datetime:
    start, _ = compute_most_recent_schedule_window(
        start_time=start_time,
        end_time=end_time,
        now=now,
    )
    return start


def compute_most_recent_schedule_window(
    *,
    start_time: time,
    end_time: time,
    now: datetime,
) -> tuple[datetime, datetime]:
    today_start = _combine(now, start_time)
    today_end = _combine(now, end_time)
    now_time = now.time()

    if start_time == end_time:
        return today_start, today_start
    if start_time < end_time:
        if now_time < start_time:
            return today_start - timedelta(days=1), today_end - timedelta(days=1)
        if now_time < end_time:
            return today_start, now
        return today_start, today_end
    if now_time >= start_time:
        return today_start, now
    if now_time < end_time:
        return today_start - timedelta(days=1), now
    return today_start - timedelta(days=1), today_end


def _schedule_mode_at(*, start: time, end: time, now_time: time) -> Mode:
    if start == end:
        return "off"
    if start < end:
        return "on" if start <= now_time < end else "off"
    # Wrap-around: window is [start, 24:00) U [00:00, end).
    return "on" if now_time >= start or now_time < end else "off"


def _combine(now: datetime, clock_time: time) -> datetime:
    return now.replace(
        hour=clock_time.hour,
        minute=clock_time.minute,
        second=clock_time.second,
        microsecond=clock_time.microsecond,
    )
