import shutil
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.repositories.manual_hold_repository import ManualHoldRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.operation_state_repository import OperationStateRepository
from app.repositories.sqlite import init_db
from app.repositories.tenant_repository import TenantRepository
from app.services.conversation_handoff_service import ConversationHandoffService


TPE = timezone(timedelta(hours=8))


@pytest.fixture
def database_path() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-handoff-service")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "handoff-service-tests.db"
    try:
        init_db(path)
        yield path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            parent_dir.rmdir()
        except OSError:
            pass


class _Clock:
    def __init__(self, initial: datetime) -> None:
        self.now = initial

    def __call__(self) -> datetime:
        return self.now


def _create_tenant(database_path: Path) -> int:
    return TenantRepository(database_path).create_tenant(
        slug="tenant-a",
        name="Tenant A",
        timezone="Asia/Taipei",
        default_language="zh-TW",
    )


def _service(database_path: Path, clock: _Clock) -> ConversationHandoffService:
    return ConversationHandoffService(
        hold_repo=ManualHoldRepository(database_path),
        message_repo=MessageRepository(database_path),
        operation_state_repo=OperationStateRepository(database_path),
        now_provider=clock,
    )


def _seed_message(
    database_path: Path,
    *,
    tenant_id: int,
    platform_user_id: str,
    display_name: str,
    created_at: datetime,
) -> None:
    conn_repo = MessageRepository(database_path)
    message_id = conn_repo.create_message(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id=platform_user_id,
        customer_display_name=display_name,
        message_text="hi",
        category="quoted",
        is_night=False,
    )
    assert message_id is not None
    # Backdate created_at directly -- create_message always stamps "now".
    import sqlite3

    with sqlite3.connect(database_path) as conn:
        conn.execute(
            "UPDATE messages SET created_at = ? WHERE id = ?",
            (created_at.isoformat(), message_id),
        )
        conn.commit()


def test_not_paused_when_no_hold(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)

    assert service.is_paused(tenant_id=tenant_id, platform="line", platform_user_id="U1") is False


def test_toggle_pauses_then_resumes(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)

    first = service.toggle(
        tenant_id=tenant_id, tenant_timezone="Asia/Taipei", platform="line", platform_user_id="U1"
    )
    assert first == "paused"
    assert service.is_paused(tenant_id=tenant_id, platform="line", platform_user_id="U1") is True

    second = service.toggle(
        tenant_id=tenant_id, tenant_timezone="Asia/Taipei", platform="line", platform_user_id="U1"
    )
    assert second == "resumed"
    assert service.is_paused(tenant_id=tenant_id, platform="line", platform_user_id="U1") is False


def test_pause_survives_through_next_active_window(database_path: Path) -> None:
    """Pausing during the day (off-hours) must still protect the customer
    once the auto-on window opens tonight -- not expire right as it starts."""
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)
    service.toggle(
        tenant_id=tenant_id, tenant_timezone="Asia/Taipei", platform="line", platform_user_id="U1"
    )

    clock.now = datetime(2026, 5, 12, 23, 30, tzinfo=TPE)

    assert service.is_paused(tenant_id=tenant_id, platform="line", platform_user_id="U1") is True


def test_pause_expires_after_active_window_ends(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)
    service.toggle(
        tenant_id=tenant_id, tenant_timezone="Asia/Taipei", platform="line", platform_user_id="U1"
    )

    clock.now = datetime(2026, 5, 13, 8, 0, tzinfo=TPE)

    assert service.is_paused(tenant_id=tenant_id, platform="line", platform_user_id="U1") is False


def test_urgent_path_is_unaffected_by_pause_flag_alone(database_path: Path) -> None:
    """is_paused only reports the flag; callers (InquiryService) are
    responsible for checking urgency BEFORE consulting it -- covered in
    test_inquiry_service.py's urgent-bypasses-pause test."""
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)
    service.toggle(
        tenant_id=tenant_id, tenant_timezone="Asia/Taipei", platform="line", platform_user_id="U1"
    )

    assert service.is_paused(tenant_id=tenant_id, platform="line", platform_user_id="U1") is True


def test_resolve_by_display_name_not_found(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)

    result = service.resolve_by_display_name(
        tenant_id=tenant_id, platform="line", display_name="Wendy"
    )

    assert result.status == "not_found"


def test_resolve_by_display_name_single_match(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    _seed_message(
        database_path,
        tenant_id=tenant_id,
        platform_user_id="U1",
        display_name="Wendy",
        created_at=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
    )
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)

    result = service.resolve_by_display_name(
        tenant_id=tenant_id, platform="line", display_name="Wendy"
    )

    assert result.status == "found"
    assert result.platform_user_id == "U1"


def test_resolve_by_display_name_ambiguous_when_two_recent_matches(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    now_utc = datetime(2026, 5, 12, 6, 0, tzinfo=timezone.utc)
    _seed_message(
        database_path, tenant_id=tenant_id, platform_user_id="U1",
        display_name="Wendy", created_at=now_utc - timedelta(hours=1),
    )
    _seed_message(
        database_path, tenant_id=tenant_id, platform_user_id="U2",
        display_name="Wendy", created_at=now_utc - timedelta(hours=2),
    )
    clock = _Clock(now_utc.astimezone(TPE))
    service = _service(database_path, clock)

    result = service.resolve_by_display_name(
        tenant_id=tenant_id, platform="line", display_name="Wendy"
    )

    assert result.status == "ambiguous"
    assert {c.platform_user_id for c in result.candidates} == {"U1", "U2"}


def test_resolve_by_display_name_not_ambiguous_when_one_stale(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    now_utc = datetime(2026, 5, 12, 6, 0, tzinfo=timezone.utc)
    _seed_message(
        database_path, tenant_id=tenant_id, platform_user_id="U1",
        display_name="Wendy", created_at=now_utc - timedelta(hours=1),
    )
    _seed_message(
        database_path, tenant_id=tenant_id, platform_user_id="U2",
        display_name="Wendy", created_at=now_utc - timedelta(hours=72),
    )
    clock = _Clock(now_utc.astimezone(TPE))
    service = _service(database_path, clock)

    result = service.resolve_by_display_name(
        tenant_id=tenant_id, platform="line", display_name="Wendy"
    )

    assert result.status == "found"
    assert result.platform_user_id == "U1"
