import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.repositories.operation_state_repository import OperationStateRepository
from app.repositories.sqlite import init_db
from app.repositories.tenant_repository import TenantRepository


@pytest.fixture
def database_path() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-operation-state-repo")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "operation-state-tests.db"
    try:
        init_db(path)
        yield path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            parent_dir.rmdir()
        except OSError:
            pass


def _create_tenant(database_path: Path, slug: str) -> int:
    return TenantRepository(database_path).create_tenant(
        slug=slug,
        name=slug.title(),
        timezone="Asia/Taipei",
        default_language="zh-TW",
    )


def test_get_or_create_inserts_row_with_defaults(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = OperationStateRepository(database_path)

    row = repository.get_or_create(tenant_id)

    assert row["tenant_id"] == tenant_id
    assert row["auto_schedule_enabled"] == 1
    assert row["auto_on_start_time"] == "23:00"
    assert row["auto_on_end_time"] == "08:00"
    assert row["manual_mode"] is None
    assert row["manual_set_at"] is None
    assert row["manual_valid_until"] is None
    assert row["last_changed_by_owner_id"] is None
    assert row["updated_at"] is not None


def test_get_or_create_second_call_returns_existing_row(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = OperationStateRepository(database_path)

    first = repository.get_or_create(tenant_id)
    repository.set_schedule_enabled(tenant_id=tenant_id, enabled=False)
    second = repository.get_or_create(tenant_id)

    assert second["tenant_id"] == first["tenant_id"]
    assert second["auto_schedule_enabled"] == 0


def test_set_manual_override_writes_fields_and_updates_updated_at(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = OperationStateRepository(database_path)
    repository.get_or_create(tenant_id)
    before = repository.get_or_create(tenant_id)["updated_at"]

    repository.set_manual_override(
        tenant_id=tenant_id,
        mode="on",
        valid_until_iso="2026-05-13T08:00:00+08:00",
        owner_id=42,
    )

    row = repository.get_or_create(tenant_id)
    assert row["manual_mode"] == "on"
    assert row["manual_valid_until"] == "2026-05-13T08:00:00+08:00"
    assert row["manual_set_at"] is not None
    assert row["last_changed_by_owner_id"] == 42
    assert row["updated_at"] >= before


def test_set_manual_override_rejects_invalid_mode(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = OperationStateRepository(database_path)
    repository.get_or_create(tenant_id)

    with pytest.raises(ValueError):
        repository.set_manual_override(
            tenant_id=tenant_id,
            mode="maybe",
            valid_until_iso="2026-05-13T08:00:00+08:00",
            owner_id=None,
        )


def test_clear_manual_override_resets_fields(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = OperationStateRepository(database_path)
    repository.get_or_create(tenant_id)
    repository.set_manual_override(
        tenant_id=tenant_id,
        mode="off",
        valid_until_iso="2026-05-13T08:00:00+08:00",
        owner_id=7,
    )

    repository.clear_manual_override(tenant_id=tenant_id)

    row = repository.get_or_create(tenant_id)
    assert row["manual_mode"] is None
    assert row["manual_set_at"] is None
    assert row["manual_valid_until"] is None


def test_set_schedule_enabled_toggles(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = OperationStateRepository(database_path)
    repository.get_or_create(tenant_id)

    repository.set_schedule_enabled(tenant_id=tenant_id, enabled=False)
    assert repository.get_or_create(tenant_id)["auto_schedule_enabled"] == 0

    repository.set_schedule_enabled(tenant_id=tenant_id, enabled=True)
    assert repository.get_or_create(tenant_id)["auto_schedule_enabled"] == 1


def test_set_schedule_window_writes_hhmm(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = OperationStateRepository(database_path)
    repository.get_or_create(tenant_id)

    repository.set_schedule_window(
        tenant_id=tenant_id,
        start_hhmm="22:30",
        end_hhmm="09:00",
    )

    row = repository.get_or_create(tenant_id)
    assert row["auto_on_start_time"] == "22:30"
    assert row["auto_on_end_time"] == "09:00"


@pytest.mark.parametrize(
    "start_hhmm,end_hhmm",
    [
        ("25:00", "08:00"),
        ("8:00", "18:00"),
        ("23:00", "8:00"),
        ("23:60", "08:00"),
        ("not-a-time", "08:00"),
    ],
)
def test_set_schedule_window_rejects_malformed_values(
    database_path: Path, start_hhmm: str, end_hhmm: str
) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = OperationStateRepository(database_path)
    repository.get_or_create(tenant_id)

    with pytest.raises(ValueError):
        repository.set_schedule_window(
            tenant_id=tenant_id,
            start_hhmm=start_hhmm,
            end_hhmm=end_hhmm,
        )


def test_cross_tenant_isolation(database_path: Path) -> None:
    tenant_a_id = _create_tenant(database_path, "tenant-a")
    tenant_b_id = _create_tenant(database_path, "tenant-b")
    repository = OperationStateRepository(database_path)

    repository.get_or_create(tenant_a_id)
    repository.set_manual_override(
        tenant_id=tenant_a_id,
        mode="on",
        valid_until_iso="2026-05-13T08:00:00+08:00",
        owner_id=1,
    )

    tenant_b_row = repository.get_or_create(tenant_b_id)
    assert tenant_b_row["tenant_id"] == tenant_b_id
    assert tenant_b_row["manual_mode"] is None
    assert tenant_b_row["manual_valid_until"] is None
    assert tenant_b_row["last_changed_by_owner_id"] is None

    tenant_a_row = repository.get_or_create(tenant_a_id)
    assert tenant_a_row["manual_mode"] == "on"
