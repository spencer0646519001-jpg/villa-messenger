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
from datetime import datetime, timedelta, timezone
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


def _state_rows(repo: ConversationStateRepository) -> list[dict]:
    with get_connection(repo.database_path) as conn:
        rows = conn.execute("SELECT * FROM conversation_states ORDER BY id").fetchall()
        return [dict(row) for row in rows]


def _force_expires_at(
    repo: ConversationStateRepository, state_id: int, expires_at: str
) -> None:
    with get_connection(repo.database_path) as conn:
        conn.execute(
            "UPDATE conversation_states SET expires_at = ? WHERE id = ?",
            (expires_at, state_id),
        )
        conn.commit()


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


def test_full_date_range_opens_state_even_when_intent_stays_ambiguous(
    repo: ConversationStateRepository,
) -> None:
    # eval candidate_40 regression: this message's own inquiry_type correctly
    # stays "unknown" (no booking keyword, no active state yet to continue),
    # but it states a complete checkin+checkout range -- strong enough
    # booking-slot evidence to open a state, so the dates aren't silently
    # discarded for lack of anywhere to land.
    service = ConversationStateService(repo)
    text = "是的\n訂8/2～8/4兩晚的"

    service.record(message=_message(text), decision=_decision(text))

    state = _active(repo)
    assert state is not None
    assert state["checkin_date"] == "2026-08-02"
    assert state["checkout_date"] == "2026-08-04"
    assert state["intent"] == "unknown"


def test_full_date_range_does_not_open_state_when_confidently_classified_elsewhere(
    repo: ConversationStateRepository,
) -> None:
    # Codex review of commit 3409642 (P2): the date-range bypass must not
    # fire for a message the pipeline confidently routed elsewhere (here,
    # faq) just because it also happens to contain two dates -- only a
    # genuinely UNCLASSIFIED (intent=="unknown") message qualifies.
    service = ConversationStateService(repo)
    text = "8/2到8/4有Wi-Fi嗎"

    service.record(message=_message(text), decision=_decision(text))

    assert _active(repo) is None


def test_full_date_range_does_not_open_state_on_an_urgent_message(
    repo: ConversationStateRepository,
) -> None:
    # Codex review of commit 3409642 (P2): an urgent safety message that
    # happens to mention dates in passing must not open a booking state.
    service = ConversationStateService(repo)
    text = "8/2到8/4瓦斯漏氣"

    service.record(message=_message(text), decision=_decision(text))

    assert _active(repo) is None


def test_single_bare_date_does_not_open_state_on_its_own(
    repo: ConversationStateRepository,
) -> None:
    # A single date alone is weaker evidence than a full range -- stays
    # unopened, same as before, so this doesn't get more permissive than the
    # one case that actually needs it.
    service = ConversationStateService(repo)
    text = "5/12"

    service.record(message=_message(text), decision=_decision(text))

    assert _active(repo) is None


def test_stale_in_progress_for_same_user_expires_before_opening_new_state(
    repo: ConversationStateRepository,
) -> None:
    service = ConversationStateService(repo)
    stale = repo.create(tenant_id=1, platform="line", platform_user_id="Uguest")
    other_user = repo.create(tenant_id=1, platform="line", platform_user_id="Uother")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _force_expires_at(repo, stale, past)
    _force_expires_at(repo, other_user, past)

    service.record(message=_message("5/12 入住 5/14 退房 多少錢?"),
                   decision=_decision("5/12 入住 5/14 退房 多少錢?"))

    rows = _state_rows(repo)
    active = _active(repo)
    assert [row["status"] for row in rows] == ["expired", "in_progress", "in_progress"]
    assert active["id"] != stale
    assert active["checkin_date"] == "2026-05-12"
    assert next(row for row in rows if row["id"] == other_user)["status"] == "in_progress"


def test_completed_state_does_not_block_same_user_new_round(
    repo: ConversationStateRepository,
) -> None:
    service = ConversationStateService(repo)
    completed = repo.create(tenant_id=1, platform="line", platform_user_id="Uguest")
    repo.mark_completed(tenant_id=1, state_id=completed)

    service.record(message=_message("5/12 入住 5/14 退房 多少錢?"),
                   decision=_decision("5/12 入住 5/14 退房 多少錢?"))

    rows = _state_rows(repo)
    active = _active(repo)
    assert [row["status"] for row in rows] == ["completed", "in_progress"]
    assert active["id"] != completed
    assert active["checkin_date"] == "2026-05-12"


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


@pytest.mark.parametrize("text", ["4", "四", "开4"])
def test_room_count_answer_merges_only_when_waiting_for_room_count(
    repo: ConversationStateRepository, text: str
) -> None:
    service = ConversationStateService(repo)
    service.record(message=_message("5/12 入住 5/14 退房 13 大人 多少錢?"),
                   decision=_decision("5/12 入住 5/14 退房 13 大人 多少錢?"))

    service.record(message=_message(text), decision=_decision(text))

    state = _active(repo)
    assert state["room_count"] == 4
    assert state["adult_count"] == 13
    assert _row_count(repo) == 1


@pytest.mark.parametrize("text", ["4", "4人"])
def test_room_count_answer_does_not_merge_before_room_count_gate(
    repo: ConversationStateRepository, text: str
) -> None:
    service = ConversationStateService(repo)
    service.record(message=_message("5/12 入住 多少錢"),
                   decision=_decision("5/12 入住 多少錢"))

    service.record(message=_message(text), decision=_decision(text))

    state = _active(repo)
    assert state["room_count"] is None
    assert _row_count(repo) == 1


def test_followup_pet_count_merges_into_active_state(repo: ConversationStateRepository) -> None:
    service = ConversationStateService(repo)
    service.record(
        message=_message("5/12 入住 5/14 退房 4 大人 有養狗 多少錢"),
        decision=_decision("5/12 入住 5/14 退房 4 大人 有養狗 多少錢"),
    )
    state = _active(repo)
    assert bool(state["has_pet"]) is True
    assert state["pet_count"] is None

    service.record(message=_message("1隻"), decision=_decision("1隻"))

    state = _active(repo)
    assert state["pet_count"] == 1
    assert state["adult_count"] == 4
    assert _row_count(repo) == 1


@pytest.mark.parametrize("text", ["2隻", "兩隻", "2"])
def test_pet_count_answer_merges_only_when_waiting_for_pet_count(
    repo: ConversationStateRepository, text: str
) -> None:
    service = ConversationStateService(repo)
    service.record(
        message=_message("5/12 入住 5/14 退房 4 大人 有養狗 多少錢"),
        decision=_decision("5/12 入住 5/14 退房 4 大人 有養狗 多少錢"),
    )

    service.record(message=_message(text), decision=_decision(text))

    state = _active(repo)
    assert state["pet_count"] == 2
    assert _row_count(repo) == 1


def test_pet_count_answer_does_not_merge_before_pet_count_gate(
    repo: ConversationStateRepository,
) -> None:
    # No pet mentioned yet -- has_pet is still False, so a bare number must
    # NOT be misread as a pet count answer.
    service = ConversationStateService(repo)
    service.record(message=_message("5/12 入住 多少錢"),
                   decision=_decision("5/12 入住 多少錢"))

    service.record(message=_message("2隻"), decision=_decision("2隻"))

    state = _active(repo)
    assert state["pet_count"] is None
    assert _row_count(repo) == 1


def test_followup_bbq_merges_into_active_state(repo: ConversationStateRepository) -> None:
    service = ConversationStateService(repo)
    service.record(
        message=_message("5/12 入住 5/14 退房 4 大人 要烤肉 多少錢"),
        decision=_decision("5/12 入住 5/14 退房 4 大人 要烤肉 多少錢"),
    )

    state = _active(repo)
    assert bool(state["wants_bbq"]) is True
    assert state["adult_count"] == 4
    assert _row_count(repo) == 1


def test_bbq_not_mentioned_leaves_wants_bbq_false(repo: ConversationStateRepository) -> None:
    service = ConversationStateService(repo)
    service.record(message=_message("5/12 入住 5/14 退房 多少錢?"),
                   decision=_decision("5/12 入住 5/14 退房 多少錢?"))

    state = _active(repo)
    assert bool(state["wants_bbq"]) is False


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
# LAYER 2: accumulated_while_off / last_off_mode_update_at
# ============================================================


def _off_inquiry_service() -> InquiryService:
    class _FakeOffOperationModeService:
        def is_system_active(self, *, tenant_id: int, tenant_timezone: str) -> bool:
            return False

    return InquiryService(
        operation_mode_service=_FakeOffOperationModeService(),
        tenant_pricing_loader=lambda tid: _PRICING,
        tenant_special_dates_loader=lambda tid: {},
        tenant_room_policy_loader=lambda tid: _ROOM_POLICY,
    )


def _off_decision(text: str) -> InquiryDecision:
    return _off_inquiry_service().handle_message(message=_message(text))


def test_off_mode_open_sets_accumulated_flag_and_timestamp(
    repo: ConversationStateRepository,
) -> None:
    service = ConversationStateService(repo)

    service.record(
        message=_message("5/12 入住 5/14 退房 多少錢?"),
        decision=_off_decision("5/12 入住 5/14 退房 多少錢?"),
    )

    state = _active(repo)
    assert state["accumulated_while_off"] == 1
    assert state["last_off_mode_update_at"] is not None
    assert state["checkin_date"] == "2026-05-12"


def test_on_mode_open_leaves_accumulated_flag_clear(
    repo: ConversationStateRepository,
) -> None:
    service = ConversationStateService(repo)

    service.record(
        message=_message("5/12 入住 5/14 退房 多少錢?"),
        decision=_decision("5/12 入住 5/14 退房 多少錢?"),
    )

    state = _active(repo)
    assert state["accumulated_while_off"] == 0
    assert state["last_off_mode_update_at"] is None


def test_on_mode_update_does_not_clear_flag_set_earlier_off(
    repo: ConversationStateRepository,
) -> None:
    service = ConversationStateService(repo)
    service.record(
        message=_message("5/12 入住 多少錢?"), decision=_off_decision("5/12 入住 多少錢?")
    )
    off_state = _active(repo)

    service.record(
        message=_message("2 大人"), decision=_decision("2 大人")
    )

    state = _active(repo)
    assert state["accumulated_while_off"] == 1
    assert state["last_off_mode_update_at"] == off_state["last_off_mode_update_at"]
    assert state["adult_count"] == 2


def test_off_mode_second_touch_advances_timestamp(
    repo: ConversationStateRepository,
) -> None:
    service = ConversationStateService(repo)
    service.record(
        message=_message("5/12 入住 多少錢?"), decision=_off_decision("5/12 入住 多少錢?")
    )
    first = _active(repo)

    service.record(
        message=_message("2 大人"), decision=_off_decision("2 大人")
    )

    second = _active(repo)
    assert second["last_off_mode_update_at"] >= first["last_off_mode_update_at"]
    assert second["accumulated_while_off"] == 1


def test_clear_accumulated_while_off(repo: ConversationStateRepository) -> None:
    service = ConversationStateService(repo)
    service.record(
        message=_message("5/12 入住 多少錢?"), decision=_off_decision("5/12 入住 多少錢?")
    )
    state = _active(repo)

    service.clear_accumulated_while_off(tenant_id=1, state_id=state["id"])

    after = _active(repo)
    assert after["accumulated_while_off"] == 0


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
