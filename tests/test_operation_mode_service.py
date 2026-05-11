import inspect
import shutil
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.repositories.operation_state_repository import OperationStateRepository
from app.repositories.sqlite import init_db
from app.repositories.tenant_repository import TenantRepository
from app.services.operation_mode_service import OperationModeService


TPE = timezone(timedelta(hours=8))


@pytest.fixture
def database_path() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-operation-mode-service")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "operation-mode-service-tests.db"
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


def _service(database_path: Path, clock: _Clock) -> OperationModeService:
    return OperationModeService(
        repo=OperationStateRepository(database_path),
        now_provider=clock,
    )


def _active(service: OperationModeService, tenant_id: int) -> bool:
    return service.is_system_active(
        tenant_id=tenant_id, tenant_timezone="Asia/Taipei"
    )


def test_fresh_tenant_at_14_is_inactive(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)

    assert _active(service, tenant_id) is False


def test_fresh_tenant_at_23_30_is_active(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 23, 30, tzinfo=TPE))
    service = _service(database_path, clock)

    assert _active(service, tenant_id) is True


def test_fresh_tenant_at_07_59_is_active(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 7, 59, tzinfo=TPE))
    service = _service(database_path, clock)

    assert _active(service, tenant_id) is True


def test_fresh_tenant_at_08_00_is_inactive(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 8, 0, tzinfo=TPE))
    service = _service(database_path, clock)

    assert _active(service, tenant_id) is False


def test_turn_on_at_14_makes_active_at_15(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)
    service.turn_on(tenant_id=tenant_id, tenant_timezone="Asia/Taipei")

    clock.now = datetime(2026, 5, 12, 15, 0, tzinfo=TPE)

    assert _active(service, tenant_id) is True


def test_turn_on_at_14_still_active_at_23_30(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)
    service.turn_on(tenant_id=tenant_id, tenant_timezone="Asia/Taipei")

    clock.now = datetime(2026, 5, 12, 23, 30, tzinfo=TPE)

    assert _active(service, tenant_id) is True


def test_turn_on_at_14_inactive_next_day_at_09(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)
    service.turn_on(tenant_id=tenant_id, tenant_timezone="Asia/Taipei")

    clock.now = datetime(2026, 5, 13, 9, 0, tzinfo=TPE)

    assert _active(service, tenant_id) is False


def test_manual_override_expires_at_boundary_and_clears_db(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    repo = OperationStateRepository(database_path)
    service = OperationModeService(repo=repo, now_provider=clock)
    service.turn_on(tenant_id=tenant_id, tenant_timezone="Asia/Taipei")

    clock.now = datetime(2026, 5, 13, 8, 0, tzinfo=TPE)
    _active(service, tenant_id)

    clock.now = datetime(2026, 5, 13, 9, 0, tzinfo=TPE)
    result = _active(service, tenant_id)
    row = repo.get_or_create(tenant_id)

    assert result is False
    assert row["manual_mode"] is None
    assert row["manual_valid_until"] is None
    assert row["manual_set_at"] is None


def test_disable_schedule_no_manual_always_inactive(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 23, 30, tzinfo=TPE))
    service = _service(database_path, clock)
    service.disable_schedule(tenant_id=tenant_id)

    assert _active(service, tenant_id) is False

    clock.now = datetime(2026, 5, 13, 2, 0, tzinfo=TPE)
    assert _active(service, tenant_id) is False


def test_disable_schedule_with_manual_on_until_expiry(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)
    service.disable_schedule(tenant_id=tenant_id)
    service.turn_on(tenant_id=tenant_id, tenant_timezone="Asia/Taipei")

    clock.now = datetime(2026, 5, 12, 22, 59, tzinfo=TPE)
    assert _active(service, tenant_id) is True

    clock.now = datetime(2026, 5, 12, 23, 30, tzinfo=TPE)
    assert _active(service, tenant_id) is False


def test_clear_manual_reverts_to_pure_schedule(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    clock = _Clock(datetime(2026, 5, 12, 14, 0, tzinfo=TPE))
    service = _service(database_path, clock)
    service.turn_on(tenant_id=tenant_id, tenant_timezone="Asia/Taipei")
    assert _active(service, tenant_id) is True

    service.clear_manual(tenant_id=tenant_id)

    assert _active(service, tenant_id) is False


def test_no_service_method_exceeds_15_lines() -> None:
    """Guard against future god-method drift."""
    for name, method in inspect.getmembers(
        OperationModeService, predicate=inspect.isfunction
    ):
        if name.startswith("_") and name != "__init__":
            continue
        if name == "__init__":
            continue
        source = inspect.getsource(method)
        lines = [
            line
            for line in source.split("\n")
            if line.strip() and not line.strip().startswith('"""')
        ]
        body_lines = len(lines) - 1
        assert body_lines <= 15, f"{name} has {body_lines} body lines, max is 15"
