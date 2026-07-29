"""
conversation_handoff_resolver — pure time-window logic for a per-customer
manual "pause" (the owner told the bot to stay out of one specific
conversation). Mirrors operation_mode_resolver.py's shape: a snapshot model
in, a bool out, zero I/O.
"""

from datetime import datetime

from pydantic import BaseModel


class ManualHoldSnapshot(BaseModel):
    paused_until: datetime | None = None


def is_paused(*, snapshot: ManualHoldSnapshot, now: datetime) -> bool:
    return snapshot.paused_until is not None and now < snapshot.paused_until
