from datetime import datetime
import shutil
import uuid
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from app.repositories.contact_repository import ContactRepository
from app.repositories.inquiry_repository import InquiryRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.sqlite import get_connection, init_db
from app.repositories.tenant_repository import TenantRepository


@pytest.fixture
def database_path() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-repositories")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "repository-tests.db"
    try:
        init_db(path)
        yield path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            parent_dir.rmdir()
        except OSError:
            pass


def test_create_and_fetch_tenant_by_slug(database_path: Path) -> None:
    repository = TenantRepository(database_path)

    tenant_id = repository.create_tenant(
        slug="ocean-villa",
        name="Ocean Villa",
        timezone="Asia/Taipei",
        default_language="zh-TW",
        emergency_phone="0912-345-678",
    )

    tenant = repository.get_by_slug("ocean-villa")

    assert tenant is not None
    assert tenant["id"] == tenant_id
    assert tenant["name"] == "Ocean Villa"
    assert tenant["timezone"] == "Asia/Taipei"
    assert tenant["default_language"] == "zh-TW"
    assert tenant["emergency_phone"] == "0912-345-678"
    assert datetime.fromisoformat(tenant["created_at"]).tzinfo is not None
    assert datetime.fromisoformat(tenant["updated_at"]).tzinfo is not None
    assert repository.get_by_id(tenant_id) == tenant


def test_contact_get_or_create_returns_same_id_for_same_tenant_platform_user(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = ContactRepository(database_path)

    first_id = repository.get_or_create_contact(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id="user-1",
        display_name="Guest One",
    )
    second_id = repository.get_or_create_contact(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id="user-1",
        display_name="Guest One Again",
    )

    contact = repository.get_by_platform_user(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id="user-1",
    )

    assert second_id == first_id
    assert contact is not None
    assert contact["id"] == first_id
    assert contact["tenant_id"] == tenant_id


def test_same_platform_user_id_can_exist_in_different_tenants(
    database_path: Path,
) -> None:
    tenant_a_id = _create_tenant(database_path, "tenant-a")
    tenant_b_id = _create_tenant(database_path, "tenant-b")
    repository = ContactRepository(database_path)

    tenant_a_contact_id = repository.get_or_create_contact(
        tenant_id=tenant_a_id,
        platform="line",
        platform_user_id="shared-user",
    )
    tenant_b_contact_id = repository.get_or_create_contact(
        tenant_id=tenant_b_id,
        platform="line",
        platform_user_id="shared-user",
    )

    assert tenant_b_contact_id != tenant_a_contact_id
    assert (
        repository.get_by_platform_user(
            tenant_id=tenant_a_id,
            platform="line",
            platform_user_id="shared-user",
        )["id"]
        == tenant_a_contact_id
    )
    assert (
        repository.get_by_platform_user(
            tenant_id=tenant_b_id,
            platform="line",
            platform_user_id="shared-user",
        )["id"]
        == tenant_b_contact_id
    )


def test_message_get_by_id_is_tenant_safe(database_path: Path) -> None:
    tenant_a_id = _create_tenant(database_path, "tenant-a")
    tenant_b_id = _create_tenant(database_path, "tenant-b")
    repository = MessageRepository(database_path)

    message_id = repository.create_message(
        tenant_id=tenant_a_id,
        platform="line",
        platform_user_id="guest-1",
        message_text="Need towels",
        category="guest_request",
        is_night=True,
    )

    assert repository.get_by_id(tenant_a_id, message_id)["id"] == message_id
    assert repository.get_by_id(tenant_b_id, message_id) is None


def test_list_unhandled_only_returns_messages_for_given_tenant(
    database_path: Path,
) -> None:
    tenant_a_id = _create_tenant(database_path, "tenant-a")
    tenant_b_id = _create_tenant(database_path, "tenant-b")
    repository = MessageRepository(database_path)

    tenant_a_unhandled_id = repository.create_message(
        tenant_id=tenant_a_id,
        platform="line",
        platform_user_id="guest-1",
        message_text="Late check-in question",
        category="question",
        is_night=True,
    )
    repository.create_message(
        tenant_id=tenant_a_id,
        platform="line",
        platform_user_id="guest-2",
        message_text="Already handled",
        category="question",
        is_night=False,
        handled=True,
    )
    repository.create_message(
        tenant_id=tenant_b_id,
        platform="line",
        platform_user_id="guest-1",
        message_text="Other tenant message",
        category="question",
        is_night=True,
    )

    messages = repository.list_unhandled(tenant_a_id)

    assert [message["id"] for message in messages] == [tenant_a_unhandled_id]


def test_inquiry_get_by_id_is_tenant_safe(database_path: Path) -> None:
    tenant_a_id = _create_tenant(database_path, "tenant-a")
    tenant_b_id = _create_tenant(database_path, "tenant-b")
    repository = InquiryRepository(database_path)

    inquiry_id = repository.create_inquiry(
        tenant_id=tenant_a_id,
        platform="messenger",
        platform_user_id="guest-1",
        inquiry_type="availability",
        original_message="Is July 1 available?",
    )

    assert repository.get_by_id(tenant_a_id, inquiry_id)["id"] == inquiry_id
    assert repository.get_by_id(tenant_b_id, inquiry_id) is None


def test_list_open_only_returns_inquiries_for_given_tenant(
    database_path: Path,
) -> None:
    tenant_a_id = _create_tenant(database_path, "tenant-a")
    tenant_b_id = _create_tenant(database_path, "tenant-b")
    repository = InquiryRepository(database_path)

    tenant_a_open_id = repository.create_inquiry(
        tenant_id=tenant_a_id,
        platform="messenger",
        platform_user_id="guest-1",
        inquiry_type="price",
        original_message="How much for two nights?",
    )
    repository.create_inquiry(
        tenant_id=tenant_a_id,
        platform="messenger",
        platform_user_id="guest-2",
        inquiry_type="price",
        original_message="Closed inquiry",
        status="closed",
    )
    repository.create_inquiry(
        tenant_id=tenant_b_id,
        platform="messenger",
        platform_user_id="guest-1",
        inquiry_type="price",
        original_message="Other tenant inquiry",
    )

    inquiries = repository.list_open(tenant_a_id)

    assert [inquiry["id"] for inquiry in inquiries] == [tenant_a_open_id]


def test_repositories_use_provided_database_path(database_path: Path) -> None:
    assert database_path.name != "homestay.db"
    assert database_path.parent.name != "data"

    repository = TenantRepository(database_path)
    tenant_id = repository.create_tenant(
        slug="temp-db-tenant",
        name="Temp DB Tenant",
        timezone="Asia/Taipei",
        default_language="zh-TW",
    )

    assert repository.get_by_id(tenant_id)["slug"] == "temp-db-tenant"


def _create_tenant(database_path: Path, slug: str) -> int:
    return TenantRepository(database_path).create_tenant(
        slug=slug,
        name=slug.title(),
        timezone="Asia/Taipei",
        default_language="zh-TW",
    )


# ============================================================
# system_state_at_time column
# ============================================================


def test_message_default_system_state_at_time_is_unknown(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = MessageRepository(database_path)

    message_id = repository.create_message(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id="guest-1",
        message_text="hi",
        category="question",
        is_night=False,
    )

    assert repository.get_by_id(tenant_id, message_id)["system_state_at_time"] == "unknown"


def test_message_explicit_system_state_at_time_is_persisted(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = MessageRepository(database_path)

    message_id = repository.create_message(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id="guest-1",
        message_text="hi",
        category="question",
        is_night=False,
        system_state_at_time="off",
    )

    assert repository.get_by_id(tenant_id, message_id)["system_state_at_time"] == "off"


# ============================================================
# Transactional connection parameter
# ============================================================


def test_message_create_with_external_connection_does_not_commit(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = MessageRepository(database_path)

    with closing(get_connection(database_path)) as shared_conn:
        message_id = repository.create_message(
            tenant_id=tenant_id,
            platform="line",
            platform_user_id="guest-1",
            message_text="hi",
            category="question",
            is_night=False,
            connection=shared_conn,
        )
        # Row is visible to the same connection but not yet committed.
        with closing(get_connection(database_path)) as outside_conn:
            row = outside_conn.execute(
                "SELECT id FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            assert row is None
        shared_conn.commit()

    assert repository.get_by_id(tenant_id, message_id)["id"] == message_id


def test_message_create_with_external_connection_rollback_discards_row(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = MessageRepository(database_path)

    with closing(get_connection(database_path)) as shared_conn:
        message_id = repository.create_message(
            tenant_id=tenant_id,
            platform="line",
            platform_user_id="guest-1",
            message_text="will be rolled back",
            category="question",
            is_night=False,
            connection=shared_conn,
        )
        shared_conn.rollback()

    assert repository.get_by_id(tenant_id, message_id) is None


def test_inquiry_create_with_external_connection_does_not_commit(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = InquiryRepository(database_path)

    with closing(get_connection(database_path)) as shared_conn:
        inquiry_id = repository.create_inquiry(
            tenant_id=tenant_id,
            platform="line",
            platform_user_id="guest-1",
            inquiry_type="price",
            original_message="how much?",
            connection=shared_conn,
        )
        with closing(get_connection(database_path)) as outside_conn:
            row = outside_conn.execute(
                "SELECT id FROM inquiries WHERE id = ?",
                (inquiry_id,),
            ).fetchone()
            assert row is None
        shared_conn.commit()

    assert repository.get_by_id(tenant_id, inquiry_id)["id"] == inquiry_id


def test_inquiry_create_with_external_connection_rollback_discards_row(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    repository = InquiryRepository(database_path)

    with closing(get_connection(database_path)) as shared_conn:
        inquiry_id = repository.create_inquiry(
            tenant_id=tenant_id,
            platform="line",
            platform_user_id="guest-1",
            inquiry_type="price",
            original_message="how much?",
            connection=shared_conn,
        )
        shared_conn.rollback()

    assert repository.get_by_id(tenant_id, inquiry_id) is None


def test_message_and_inquiry_share_transaction_via_external_connection(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "tenant-a")
    message_repo = MessageRepository(database_path)
    inquiry_repo = InquiryRepository(database_path)

    with closing(get_connection(database_path)) as shared_conn:
        with shared_conn:  # transaction
            message_id = message_repo.create_message(
                tenant_id=tenant_id,
                platform="line",
                platform_user_id="guest-1",
                message_text="how much?",
                category="quoted",
                is_night=False,
                connection=shared_conn,
            )
            inquiry_id = inquiry_repo.create_inquiry(
                tenant_id=tenant_id,
                platform="line",
                platform_user_id="guest-1",
                inquiry_type="price",
                original_message="how much?",
                message_id=message_id,
                connection=shared_conn,
            )

    assert message_repo.get_by_id(tenant_id, message_id)["id"] == message_id
    persisted_inquiry = inquiry_repo.get_by_id(tenant_id, inquiry_id)
    assert persisted_inquiry["id"] == inquiry_id
    assert persisted_inquiry["message_id"] == message_id
