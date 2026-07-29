from datetime import datetime, timedelta, timezone

from app.domain.conversation_handoff_resolver import ManualHoldSnapshot, is_paused


TPE = timezone(timedelta(hours=8))


def test_no_hold_is_not_paused() -> None:
    snapshot = ManualHoldSnapshot(paused_until=None)
    now = datetime(2026, 5, 12, 14, 0, tzinfo=TPE)

    assert is_paused(snapshot=snapshot, now=now) is False


def test_hold_valid_until_future_is_paused() -> None:
    now = datetime(2026, 5, 12, 14, 0, tzinfo=TPE)
    snapshot = ManualHoldSnapshot(paused_until=datetime(2026, 5, 12, 23, 0, tzinfo=TPE))

    assert is_paused(snapshot=snapshot, now=now) is True


def test_hold_exactly_at_expiry_is_not_paused() -> None:
    expiry = datetime(2026, 5, 12, 23, 0, tzinfo=TPE)
    snapshot = ManualHoldSnapshot(paused_until=expiry)

    assert is_paused(snapshot=snapshot, now=expiry) is False


def test_hold_past_expiry_is_not_paused() -> None:
    snapshot = ManualHoldSnapshot(paused_until=datetime(2026, 5, 12, 23, 0, tzinfo=TPE))
    now = datetime(2026, 5, 12, 23, 1, tzinfo=TPE)

    assert is_paused(snapshot=snapshot, now=now) is False
