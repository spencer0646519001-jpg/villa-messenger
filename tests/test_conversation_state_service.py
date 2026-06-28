"""
Tests for ConversationStateService (STAGE B accumulation).

Decisions are produced by the real InquiryService so the create/update gate is
exercised against the actual log_payload shape. State is read back from a real
ConversationStateRepository on a temp SQLite DB.
"""

import inspect
import shutil
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.services.conversation_state_service as service_module
from app.domain.inquiry_decision import InquiryDecision
from app.repositories.conversation_state_repository import ConversationStateRepository
from app.repositories.sqlite import get_connection, init_db
from app.repositories.tenant_repository import TenantRepository
from app.schemas import InboundMessage
from app.services.conversation_state_service import ConversationStateService
from app.services.inquiry_service import InquiryService


_PRICING = {
    "base_prices_per_night": {
        "8_people": {"weekday": 9000, "saturday": 15000, "summer_weekday": 12000,
                     "summer_saturday_or_holiday": 15000, "spring_festival": 25000},
    },
}

_ROOM_POLICY = {
    "standard_capacity": 12,
    "max_capacity": 16,
    "room_opening_rules": [
        {"max_people": 8, "rooms_opened": 2},
        {"max_people": 10, "rooms_opened": 3},
        {"max_people": 12, "rooms_opened": 4},
        {"min_people": 13, "max_people": 16, "rooms_opened": 4, "extra_beds": True},
    ],
}


class _FakeOperationModeService:
    def is_system_active(self, *, tenant_id: int, tenant_timezone: str) -> bool:
        return True


def _inquiry_service() -> InquiryService:
    return InquiryService(
        operation_mode_service=_FakeOperationModeService(),
        tenant_pricing_loader=lambda tid: _PRICING,
        tenant_special_dates_loader=lambda tid: {},
        tenant_room_policy_loader=lambda tid: _ROOM_POLICY,
    )


def _message(text: str, *, user: str = "Uguest") -> InboundMessage:
    return InboundMessage(
        tenant_id=1,
        tenant_slug="test-villa",
        tenant_timezone="Asia/Taipei",
        platform="line",
        platform_user_id=user,
        text=text,
        timestamp=datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
    )


def _decision(text: str) -> InquiryDecision:
    return _inquiry_service().handle_message(message=_message(text))


@pytest.fixture
def repo() -> Iterator[ConversationStateRepository]:
    parent_dir = Path("pytest-cache-files-convstate")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "convstate-tests.db"
    try:
        init_db(path)
        # conversation_states.tenant_id has an FK to tenants(id); seed tenant 1.
        TenantRepository(path).create_tenant(
            slug="test-villa", name="Test Villa",
            timezone="Asia/Taipei", default_language="zh-TW",
        )
        yield ConversationStateRepository(path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            parent_dir.rmdir()
        except OSError:
            pass


def _active(repo: ConversationStateRepository, user: str = "Uguest") -> dict | None:
    return repo.get_active_for_user(tenant_id=1, platform="line", platform_user_id=user)


def _row_count(repo: ConversationStateRepository) -> int:
    with get_connection(repo.database_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM conversation_states").fetchone()[0]


# ============================================================
# OPEN: quote-relevant inquiry creates a state
# ============================================================


def test_quote_relevant_inquiry_opens_state(repo: ConversationStateRepository) -> None:
    service = ConversationStateService(repo)

    service.record(message=_message("5/12 入住 5/14 退房 多少錢?"),
                   decision=_decision("5/12 入住 5/14 退房 多少錢?"))

    state = _active(repo)
    assert state is not None
    assert state["status"] == "in_progress"
    assert state["checkin_date"] == "2026-05-12"
    assert state["checkout_date"] == "2026-05-14"
    assert state["intent"] == "price"


# ============================================================
# UPDATE: a bare slot-bearing follow-up merges into the active state
# (the goldfish-memory fix: it need NOT classify as an inquiry itself)
# ============================================================


def test_followup_guest_count_merges_into_active_state(repo: ConversationStateRepository) -> None:
    service = ConversationStateService(repo)
    service.record(message=_message("5/12 入住 5/14 退房 多少錢?"),
                   decision=_decision("5/12 入住 5/14 退房 多少錢?"))

    service.record(message=_message("4 大人"), decision=_decision("4 大人"))

    state = _active(repo)
    assert state["adult_count"] == 4
    # earlier slots retained (COALESCE merge, not overwrite)
    assert state["checkin_date"] == "2026-05-12"
    assert state["checkout_date"] == "2026-05-14"
    # exactly one row total: the follow-up updated, it did not insert a second
    assert _row_count(repo) == 1


def test_followup_room_count_merges_into_active_state(repo: ConversationStateRepository) -> None:
    service = ConversationStateService(repo)
    service.record(message=_message("5/12 入住 5/14 退房 13 大人 多少錢?"),
                   decision=_decision("5/12 入住 5/14 退房 13 大人 多少錢?"))

    service.record(message=_message("開4房"), decision=_decision("開4房"))

    state = _active(repo)
    assert state["room_count"] == 4
    assert state["adult_count"] == 13
    assert _row_count(repo) == 1


# ============================================================
# NO-OP: non-inquiry chatter with no active state creates nothing
# ============================================================


def test_non_inquiry_with_no_active_state_creates_nothing(repo: ConversationStateRepository) -> None:
    service = ConversationStateService(repo)

    service.record(message=_message("你好"), decision=_decision("你好"))

    assert _active(repo) is None


# ============================================================
# NO-OP: chatter against an active state leaves it untouched (no slot wipe,
# no TTL refresh)
# ============================================================


def test_chatter_against_active_state_is_noop(repo: ConversationStateRepository) -> None:
    service = ConversationStateService(repo)
    service.record(message=_message("5/12 入住 5/14 退房 多少錢?"),
                   decision=_decision("5/12 入住 5/14 退房 多少錢?"))
    before = _active(repo)

    service.record(message=_message("你好"), decision=_decision("你好"))

    after = _active(repo)
    assert after["checkin_date"] == before["checkin_date"]
    assert after["adult_count"] is None
    # No update_slots call -> updated_at and expires_at unchanged (no TTL slide).
    assert after["updated_at"] == before["updated_at"]
    assert after["expires_at"] == before["expires_at"]


# ============================================================
# DISCIPLINE
# ============================================================


def test_service_methods_under_15_body_lines() -> None:
    cls = service_module.ConversationStateService
    for name, obj in inspect.getmembers(cls, inspect.isfunction):
        if obj.__module__ != service_module.__name__:
            continue
        lines = [
            line for line in inspect.getsource(obj).splitlines()[1:]
            if line.strip() and not line.strip().startswith(("#", '"""'))
        ]
        assert len(lines) <= 15, f"{name} body too long: {len(lines)} lines"
