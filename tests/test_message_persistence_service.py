import inspect
import json
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.domain.inquiry_decision import InquiryDecision
from app.repositories.inquiry_repository import InquiryRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.sqlite import get_connection, init_db
from app.repositories.tenant_repository import TenantRepository
from app.schemas import InboundMessage
from app.services.inquiry_service import InquiryService
from app.services.message_persistence_service import MessagePersistenceService


_DEFAULT_PRICING = {
    "base_prices_per_night": {
        "8_people": {
            "weekday": 9000,
            "saturday": 15000,
            "summer_weekday": 12000,
            "summer_saturday_or_holiday": 15000,
            "spring_festival": 25000,
        },
        "10_people": {
            "weekday": 12000,
            "saturday": 18000,
            "summer_weekday": 15000,
            "summer_saturday_or_holiday": 18000,
            "spring_festival": 28000,
        },
        "12_people": {
            "weekday": 15000,
            "saturday": 21000,
            "summer_weekday": 18000,
            "summer_saturday_or_holiday": 21000,
            "spring_festival": 31000,
        },
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
    def __init__(self, *, return_value: bool) -> None:
        self._return_value = return_value

    def is_system_active(self, *, tenant_id: int, tenant_timezone: str) -> bool:
        return self._return_value


@pytest.fixture
def database_path() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-persistence")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "persistence-tests.db"
    try:
        init_db(path)
        yield path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            parent_dir.rmdir()
        except OSError:
            pass


def _create_tenant(database_path: Path, slug: str = "test-villa") -> int:
    return TenantRepository(database_path).create_tenant(
        slug=slug,
        name=slug.title(),
        timezone="Asia/Taipei",
        default_language="zh-TW",
    )


def _build_message(text: str, *, tenant_id: int = 1) -> InboundMessage:
    return InboundMessage(
        tenant_id=tenant_id,
        tenant_slug="test-villa",
        tenant_timezone="Asia/Taipei",
        platform="line",
        platform_user_id="user-123",
        customer_display_name="Test User",
        text=text,
        timestamp=datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
    )


def _decision_for(
    text: str, *, system_on: bool = True, tenant_id: int = 1
) -> InquiryDecision:
    service = InquiryService(
        operation_mode_service=_FakeOperationModeService(return_value=system_on),
        tenant_pricing_loader=lambda tid: _DEFAULT_PRICING,
        tenant_special_dates_loader=lambda tid: {},
        tenant_room_policy_loader=lambda tid: _ROOM_POLICY,
    )
    return service.handle_message(message=_build_message(text, tenant_id=tenant_id))


# ============================================================
# PER-BRANCH PERSISTENCE
# ============================================================


def test_happy_quote_persists_both_rows(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("5/12 入住 5/13 退房 4 大人 開2房 多少錢?", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    result = service.persist(decision=decision)

    assert isinstance(result["message_id"], int)
    assert isinstance(result["inquiry_id"], int)
    assert MessageRepository(database_path).get_by_id(
        tenant_id, result["message_id"]
    ) is not None
    assert InquiryRepository(database_path).get_by_id(
        tenant_id, result["inquiry_id"]
    ) is not None


def test_over_capacity_persists_inquiry_without_total(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("5/12 入住 5/13 退房 17 大人 開4房 多少錢?", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    result = service.persist(decision=decision)

    inquiry = InquiryRepository(database_path).get_by_id(
        tenant_id, result["inquiry_id"]
    )
    assert inquiry["estimated_total_price"] is None


def test_invalid_date_persists_both_rows(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("5/14 入住 5/12 退房 4 大人 開2房 多少錢?", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    result = service.persist(decision=decision)

    assert result["inquiry_id"] is not None
    message = MessageRepository(database_path).get_by_id(
        tenant_id, result["message_id"]
    )
    assert message["category"] == "invalid_date"


def test_missing_info_persists_both_rows(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("多少錢?", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    result = service.persist(decision=decision)

    assert result["message_id"] is not None
    assert result["inquiry_id"] is not None


def test_non_inquiry_persists_only_messages_row(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("今天天氣不錯", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    result = service.persist(decision=decision)

    assert isinstance(result["message_id"], int)
    assert result["inquiry_id"] is None


def test_urgent_persists_only_messages_row(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("火災!", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    result = service.persist(decision=decision)

    assert isinstance(result["message_id"], int)
    assert result["inquiry_id"] is None
    message = MessageRepository(database_path).get_by_id(
        tenant_id, result["message_id"]
    )
    assert message["is_urgent"] == 1


def test_off_mode_inquiry_persists_both_rows(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for(
        "5/12 入住 5/13 退房 4 大人 開2房 多少錢?",
        system_on=False,
        tenant_id=tenant_id,
    )
    service = MessagePersistenceService(database_path=database_path)

    result = service.persist(decision=decision)

    message = MessageRepository(database_path).get_by_id(
        tenant_id, result["message_id"]
    )
    assert message["system_state_at_time"] == "off"
    assert InquiryRepository(database_path).get_by_id(
        tenant_id, result["inquiry_id"]
    ) is not None


# ============================================================
# TRANSACTION SEMANTICS
# ============================================================


def test_transaction_commits_on_success_visible_from_fresh_connection(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("5/12 入住 5/13 退房 4 大人 開2房 多少錢?", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    result = service.persist(decision=decision)

    with get_connection(database_path) as fresh:
        message = fresh.execute(
            "SELECT * FROM messages WHERE id = ?", (result["message_id"],)
        ).fetchone()
        inquiry = fresh.execute(
            "SELECT * FROM inquiries WHERE id = ?", (result["inquiry_id"],)
        ).fetchone()
    assert message is not None
    assert inquiry is not None


def test_transaction_rolls_back_message_when_inquiry_insert_fails(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("5/12 入住 5/13 退房 4 大人 開2房 多少錢?", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    def _boom(self, *args, **kwargs):
        raise sqlite3.IntegrityError("simulated inquiry insert failure")

    monkeypatch.setattr(InquiryRepository, "create_inquiry", _boom)

    with pytest.raises(sqlite3.IntegrityError):
        service.persist(decision=decision)

    with get_connection(database_path) as fresh:
        count = fresh.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()["c"]
    assert count == 0


# ============================================================
# TENANT ISOLATION
# ============================================================


def test_tenant_isolation_messages_not_visible_to_other_tenant(
    database_path: Path,
) -> None:
    tenant_one = _create_tenant(database_path, slug="tenant-one")
    tenant_two = _create_tenant(database_path, slug="tenant-two")
    decision = _decision_for("你好", tenant_id=tenant_one)
    service = MessagePersistenceService(database_path=database_path)

    service.persist(decision=decision)

    other_messages = MessageRepository(database_path).list_unhandled(tenant_two)
    assert other_messages == []


# ============================================================
# IDEMPOTENCY (none) AND ERROR PROPAGATION
# ============================================================


def test_persist_is_not_idempotent_two_calls_produce_two_rows(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("你好", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    first = service.persist(decision=decision)
    second = service.persist(decision=decision)

    assert first["message_id"] != second["message_id"]


def test_persist_raises_on_unopenable_database_path(database_path: Path) -> None:
    unreachable = database_path.parent / "does" / "not" / "exist" / "homestay.db"
    service = MessagePersistenceService(database_path=unreachable)
    decision = _decision_for("你好")

    with pytest.raises(sqlite3.Error):
        service.persist(decision=decision)


# ============================================================
# RETURN SHAPE
# ============================================================


def test_persist_returns_dict_with_both_keys(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("你好", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    result = service.persist(decision=decision)

    assert set(result.keys()) == {"message_id", "inquiry_id"}
    assert isinstance(result["message_id"], int)
    assert result["inquiry_id"] is None


# ============================================================
# ROUND TRIP (mapper-produced values match DB row)
# ============================================================


def test_inquiry_row_round_trip_matches_mapper_output(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("5/12 入住 5/13 退房 4 大人 開2房 多少錢?", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    result = service.persist(decision=decision)

    persisted = InquiryRepository(database_path).get_by_id(
        tenant_id, result["inquiry_id"]
    )
    assert persisted["tenant_id"] == tenant_id
    assert persisted["platform"] == "line"
    assert persisted["platform_user_id"] == "user-123"
    assert persisted["inquiry_type"] == "price"
    assert persisted["checkin_date"] == "2026-05-12"
    assert persisted["checkout_date"] == "2026-05-13"
    assert persisted["adult_count"] == 4
    assert persisted["estimated_total_price"] == 9000
    assert persisted["message_id"] == result["message_id"]


# ============================================================
# RAW LOG PAYLOAD ROUND TRIP (PR8 debt: recover received_at + urgency fields)
# ============================================================


def test_raw_log_payload_survives_full_persistence_round_trip(
    database_path: Path,
) -> None:
    # The key test: persist an urgent decision, read the message row back, and
    # confirm the two PR8-dropped fields (urgency_category,
    # urgency_matched_keywords) AND the original event time (received_at) are
    # all recoverable from raw_log_payload after mapper->service->repo->DB->read.
    tenant_id = _create_tenant(database_path)
    decision = _decision_for("火災!", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    result = service.persist(decision=decision)

    row = MessageRepository(database_path).get_by_id(tenant_id, result["message_id"])
    recovered = json.loads(row["raw_log_payload"])
    assert recovered["received_at"] == "2026-05-13T10:00:00+00:00"
    assert recovered["urgency_category"] == decision.log_payload["urgency_category"]
    assert (
        recovered["urgency_matched_keywords"]
        == decision.log_payload["urgency_matched_keywords"]
    )


# ============================================================
# CONNECTION LIFECYCLE
# ============================================================


def test_two_sequential_persist_calls_both_succeed(database_path: Path) -> None:
    """Indirect check that the prior connection was closed (no 'database locked')."""
    tenant_id = _create_tenant(database_path)
    decision_one = _decision_for("你好", tenant_id=tenant_id)
    decision_two = _decision_for("謝謝", tenant_id=tenant_id)
    service = MessagePersistenceService(database_path=database_path)

    first = service.persist(decision=decision_one)
    second = service.persist(decision=decision_two)

    assert first["message_id"] != second["message_id"]


# ============================================================
# DISCIPLINE
# ============================================================


def test_no_persistence_service_method_exceeds_line_budget() -> None:
    """Public methods ≤15 body lines; private helpers ≤25."""
    for name, method in inspect.getmembers(
        MessagePersistenceService, predicate=inspect.isfunction
    ):
        if name == "__init__":
            continue
        source = inspect.getsource(method)
        lines = [
            line
            for line in source.split("\n")
            if line.strip() and not line.strip().startswith('"""')
        ]
        body_lines = len(lines) - 1
        limit = 25 if name.startswith("_") else 15
        kind = "private" if name.startswith("_") else "public"
        assert body_lines <= limit, (
            f"{kind} method {name} has {body_lines} body lines, max is {limit}"
        )
