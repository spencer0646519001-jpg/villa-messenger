import ast
import inspect
import shutil
import sqlite3
import textwrap
import uuid
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.repositories.conversation_state_repository as repository_module
from app.repositories.conversation_state_repository import (
    ConversationStateRepository,
)
from app.repositories.sqlite import get_connection, init_db
from app.repositories.tenant_repository import TenantRepository


@pytest.fixture
def database_path() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-conversation-states")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "conversation-state-tests.db"
    try:
        init_db(path)
        yield path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            parent_dir.rmdir()
        except OSError:
            pass


def _create_tenant(database_path: Path, slug: str = "zhen-villa") -> int:
    return TenantRepository(database_path).create_tenant(
        slug=slug,
        name=slug.title(),
        timezone="Asia/Taipei",
        default_language="zh-TW",
    )


def _row_by_id(database_path: Path, state_id: int) -> dict | None:
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT * FROM conversation_states WHERE id = ?",
            (state_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def _force_expires_at(database_path: Path, state_id: int, expires_at: str) -> None:
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE conversation_states SET expires_at = ? WHERE id = ?",
            (expires_at, state_id),
        )
        connection.commit()


_PAST_ISO = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


# ============================================================
# CREATE / GET ROUND TRIP
# ============================================================


def test_create_then_get_active_returns_it(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)

    state_id = repository.create(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id="Uguest0001",
        intent="price",
        checkin_date="2026-07-01",
    )

    active = repository.get_active_for_user(
        tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
    )
    assert active is not None
    assert active["id"] == state_id
    assert active["status"] == "in_progress"
    assert active["intent"] == "price"
    assert active["checkin_date"] == "2026-07-01"


def test_get_active_unknown_user_returns_none(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)

    assert (
        repository.get_active_for_user(
            tenant_id=tenant_id, platform="line", platform_user_id="Unobody"
        )
        is None
    )


# ============================================================
# UPDATE_SLOTS — COALESCE MERGE
# ============================================================


def test_update_slots_merges_without_clobbering(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    state_id = repository.create(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id="Uguest0001",
        checkin_date="2026-07-01",
    )

    # Supplying ONLY adult_count must not wipe the stored checkin_date.
    repository.update_slots(tenant_id=tenant_id, state_id=state_id, adult_count=2)

    row = _row_by_id(database_path, state_id)
    assert row["adult_count"] == 2
    assert row["checkin_date"] == "2026-07-01"


def test_update_slots_none_leaves_slot_unchanged(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    state_id = repository.create(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id="Uguest0001",
        adult_count=3,
    )

    # adult_count defaults to None here -> must be left as the stored 3.
    repository.update_slots(
        tenant_id=tenant_id,
        state_id=state_id,
        checkout_date="2026-07-05",
    )

    row = _row_by_id(database_path, state_id)
    assert row["adult_count"] == 3
    assert row["checkout_date"] == "2026-07-05"


def test_update_slots_wrong_tenant_does_not_modify_state(database_path: Path) -> None:
    tenant_a_id = _create_tenant(database_path, slug="tenant-a")
    tenant_b_id = _create_tenant(database_path, slug="tenant-b")
    repository = ConversationStateRepository(database_path)
    state_id = repository.create(
        tenant_id=tenant_a_id,
        platform="line",
        platform_user_id="Uguest0001",
        checkin_date="2026-07-01",
    )

    repository.update_slots(tenant_id=tenant_b_id, state_id=state_id, adult_count=2)
    row = _row_by_id(database_path, state_id)
    assert row["adult_count"] is None
    assert row["checkin_date"] == "2026-07-01"

    repository.update_slots(tenant_id=tenant_a_id, state_id=state_id, adult_count=2)
    row = _row_by_id(database_path, state_id)
    assert row["adult_count"] == 2


# ============================================================
# SLIDING EXPIRY
# ============================================================


def test_update_slots_slides_expiry_forward(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    state_id = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
    )
    # Drive expiry into the past, then a fresh turn should push it forward.
    _force_expires_at(database_path, state_id, _PAST_ISO)

    repository.update_slots(tenant_id=tenant_id, state_id=state_id, adult_count=2)

    row = _row_by_id(database_path, state_id)
    assert row["expires_at"] > datetime.now(timezone.utc).isoformat()


def test_update_slots_no_refresh_leaves_expiry(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    state_id = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
    )
    _force_expires_at(database_path, state_id, _PAST_ISO)

    repository.update_slots(
        tenant_id=tenant_id,
        state_id=state_id,
        adult_count=2,
        refresh_expiry=False,
    )

    row = _row_by_id(database_path, state_id)
    assert row["expires_at"] == _PAST_ISO


def test_get_active_returns_none_when_expired(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    state_id = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
    )
    _force_expires_at(database_path, state_id, _PAST_ISO)

    # Row is still status='in_progress', but past expires_at hides it.
    assert (
        repository.get_active_for_user(
            tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
        )
        is None
    )


# ============================================================
# PARTIAL UNIQUE INDEX — ONE ACTIVE PER USER
# ============================================================


def test_second_active_row_is_rejected(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
    )

    with pytest.raises(sqlite3.IntegrityError):
        repository.create(
            tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
        )


def test_completed_row_does_not_block_new_active(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    first_id = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
    )
    repository.mark_completed(tenant_id=tenant_id, state_id=first_id)

    # The partial index only covers in_progress rows, so a fresh one is allowed.
    second_id = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
    )
    assert second_id != first_id
    active = repository.get_active_for_user(
        tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
    )
    assert active["id"] == second_id


def test_active_uniqueness_is_per_platform_and_user(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    # Same user id under different platforms are distinct conversations.
    line_id = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Ushared"
    )
    messenger_id = repository.create(
        tenant_id=tenant_id, platform="messenger", platform_user_id="Ushared"
    )
    assert line_id != messenger_id


# ============================================================
# STATUS TRANSITIONS + BULK EXPIRY
# ============================================================


def test_mark_completed_and_mark_expired_flip_status(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    completed_id = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
    )
    expired_id = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Uguest0002"
    )

    repository.mark_completed(tenant_id=tenant_id, state_id=completed_id)
    repository.mark_expired(tenant_id=tenant_id, state_id=expired_id)

    assert _row_by_id(database_path, completed_id)["status"] == "completed"
    assert _row_by_id(database_path, expired_id)["status"] == "expired"


def test_mark_completed_wrong_tenant_does_not_modify_state(database_path: Path) -> None:
    tenant_a_id = _create_tenant(database_path, slug="tenant-a")
    tenant_b_id = _create_tenant(database_path, slug="tenant-b")
    repository = ConversationStateRepository(database_path)
    state_id = repository.create(
        tenant_id=tenant_a_id,
        platform="line",
        platform_user_id="Uguest0001",
    )

    repository.mark_completed(tenant_id=tenant_b_id, state_id=state_id)
    assert _row_by_id(database_path, state_id)["status"] == "in_progress"

    repository.mark_completed(tenant_id=tenant_a_id, state_id=state_id)
    assert _row_by_id(database_path, state_id)["status"] == "completed"


def test_expire_stale_bulk_expires_and_returns_count(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    stale_one = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Ustale1"
    )
    stale_two = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Ustale2"
    )
    fresh = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Ufresh"
    )
    _force_expires_at(database_path, stale_one, _PAST_ISO)
    _force_expires_at(database_path, stale_two, _PAST_ISO)

    changed = repository.expire_stale(tenant_id=tenant_id)

    assert changed == 2
    assert _row_by_id(database_path, stale_one)["status"] == "expired"
    assert _row_by_id(database_path, stale_two)["status"] == "expired"
    assert _row_by_id(database_path, fresh)["status"] == "in_progress"


def test_expire_stale_only_expires_requested_tenant(database_path: Path) -> None:
    tenant_a_id = _create_tenant(database_path, slug="tenant-a")
    tenant_b_id = _create_tenant(database_path, slug="tenant-b")
    repository = ConversationStateRepository(database_path)
    tenant_a_state = repository.create(
        tenant_id=tenant_a_id,
        platform="line",
        platform_user_id="Ustale",
    )
    tenant_b_state = repository.create(
        tenant_id=tenant_b_id,
        platform="line",
        platform_user_id="Ustale",
    )
    _force_expires_at(database_path, tenant_a_state, _PAST_ISO)
    _force_expires_at(database_path, tenant_b_state, _PAST_ISO)

    changed = repository.expire_stale(tenant_id=tenant_a_id)

    assert changed == 1
    assert _row_by_id(database_path, tenant_a_state)["status"] == "expired"
    assert _row_by_id(database_path, tenant_b_state)["status"] == "in_progress"


def test_expire_stale_for_user_only_expires_requested_user(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    target = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Utarget"
    )
    other_user = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Uother"
    )
    other_platform = repository.create(
        tenant_id=tenant_id, platform="messenger", platform_user_id="Utarget"
    )
    for state_id in (target, other_user, other_platform):
        _force_expires_at(database_path, state_id, _PAST_ISO)

    changed = repository.expire_stale_for_user(
        tenant_id=tenant_id, platform="line", platform_user_id="Utarget"
    )

    assert changed == 1
    assert _row_by_id(database_path, target)["status"] == "expired"
    assert _row_by_id(database_path, other_user)["status"] == "in_progress"
    assert _row_by_id(database_path, other_platform)["status"] == "in_progress"


def test_expire_stale_for_user_uses_same_boundary_as_active_lookup(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_now = datetime(2026, 7, 2, 4, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(repository_module, "_utc_now_iso", lambda: fixed_now.isoformat())
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    future = repository.create(tenant_id=tenant_id, platform="line", platform_user_id="Ufuture")
    exact = repository.create(tenant_id=tenant_id, platform="line", platform_user_id="Uexact")
    past = repository.create(tenant_id=tenant_id, platform="line", platform_user_id="Upast")
    _force_expires_at(database_path, future, (fixed_now + timedelta(seconds=1)).isoformat())
    _force_expires_at(database_path, exact, fixed_now.isoformat())
    _force_expires_at(database_path, past, (fixed_now - timedelta(seconds=1)).isoformat())

    assert repository.expire_stale_for_user(
        tenant_id=tenant_id, platform="line", platform_user_id="Ufuture"
    ) == 0
    assert repository.expire_stale_for_user(
        tenant_id=tenant_id, platform="line", platform_user_id="Uexact"
    ) == 1
    assert repository.expire_stale_for_user(
        tenant_id=tenant_id, platform="line", platform_user_id="Upast"
    ) == 1
    assert _row_by_id(database_path, future)["status"] == "in_progress"
    assert _row_by_id(database_path, exact)["status"] == "expired"
    assert _row_by_id(database_path, past)["status"] == "expired"


# ============================================================
# has_pet VS pet_count: "no pets" DISTINCT FROM "unasked"
# ============================================================


def test_no_pets_is_distinguishable_from_unasked(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)

    no_pets_id = repository.create(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id="Uno_pets",
        has_pet=False,
        pet_count=0,
    )
    unasked_id = repository.create(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id="Uunasked",
    )

    no_pets = _row_by_id(database_path, no_pets_id)
    unasked = _row_by_id(database_path, unasked_id)
    # "No pets" is an answered 0; "unasked" is NULL. Both carry has_pet = 0.
    assert no_pets["pet_count"] == 0
    assert unasked["pet_count"] is None
    assert no_pets["has_pet"] == 0
    assert unasked["has_pet"] == 0


# ============================================================
# STATUS ENUM VALIDATION
# ============================================================


def test_set_status_rejects_invalid_status(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path)
    repository = ConversationStateRepository(database_path)
    state_id = repository.create(
        tenant_id=tenant_id, platform="line", platform_user_id="Uguest0001"
    )

    with pytest.raises(ValueError):
        repository._set_status(tenant_id, state_id, "bogus")


# ============================================================
# METHOD-LENGTH DISCIPLINE
# ============================================================


def _body_line_count(func) -> int:
    source = textwrap.dedent(inspect.getsource(func))
    funcdef = ast.parse(source).body[0]
    body = funcdef.body
    # Drop a leading docstring so prose does not count against the budget.
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        return 0
    lines = source.split("\n")[body[0].lineno - 1 : body[-1].end_lineno]
    return len([l for l in lines if l.strip() and not l.strip().startswith("#")])


@pytest.mark.parametrize(
    "func",
    [
        ConversationStateRepository.get_active_for_user,
        ConversationStateRepository.create,
        ConversationStateRepository.update_slots,
        ConversationStateRepository.mark_completed,
        ConversationStateRepository.mark_expired,
        ConversationStateRepository.expire_stale,
        ConversationStateRepository.expire_stale_for_user,
    ],
)
def test_public_methods_under_15_body_lines(func) -> None:
    assert _body_line_count(func) <= 15, (
        f"{func.__qualname__} body too long: {_body_line_count(func)} lines"
    )
