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
