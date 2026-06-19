import ast
import inspect
import shutil
import textwrap
import uuid
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from app.repositories.sqlite import get_connection, init_db
from app.repositories.tenant_owner_repository import TenantOwnerRepository
from app.repositories.tenant_repository import TenantRepository


_NOW = "2026-05-03T00:00:00+08:00"


@pytest.fixture
def database_path() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-tenant-owners")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "tenant-owner-tests.db"
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


def _insert_owner(
    database_path: Path,
    tenant_id: int,
    user_id: str,
    *,
    platform: str = "line",
    role: str = "owner",
    is_active: bool = True,
) -> None:
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            INSERT INTO tenant_owners (
                tenant_id, platform, platform_user_id, role, is_active,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, platform, user_id, role, int(is_active), _NOW, _NOW),
        )
        connection.commit()


def test_list_active_owner_user_ids_returns_all_active_owner_ids(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "zhen-villa")
    _insert_owner(database_path, tenant_id, "Uowner-a")
    _insert_owner(database_path, tenant_id, "Uowner-b")

    owner_ids = TenantOwnerRepository(database_path).list_active_owner_user_ids(
        tenant_id=tenant_id,
        platform="line",
    )

    assert owner_ids == ["Uowner-a", "Uowner-b"]


def test_list_active_owner_user_ids_filters_inactive_and_non_owner_roles(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "zhen-villa")
    _insert_owner(database_path, tenant_id, "Uowner-active")
    _insert_owner(database_path, tenant_id, "Uowner-inactive", is_active=False)
    _insert_owner(database_path, tenant_id, "Ustaff", role="staff")

    owner_ids = TenantOwnerRepository(database_path).list_active_owner_user_ids(
        tenant_id=tenant_id,
        platform="line",
    )

    assert owner_ids == ["Uowner-active"]


def test_list_active_owner_user_ids_is_tenant_and_platform_scoped(
    database_path: Path,
) -> None:
    tenant_a_id = _create_tenant(database_path, "tenant-a")
    tenant_b_id = _create_tenant(database_path, "tenant-b")
    _insert_owner(database_path, tenant_a_id, "Uline-owner")
    _insert_owner(database_path, tenant_a_id, "Umessenger-owner", platform="messenger")
    _insert_owner(database_path, tenant_b_id, "Uother-tenant-owner")

    owner_ids = TenantOwnerRepository(database_path).list_active_owner_user_ids(
        tenant_id=tenant_a_id,
        platform="line",
    )

    assert owner_ids == ["Uline-owner"]


def _body_line_count(func) -> int:
    source = textwrap.dedent(inspect.getsource(func))
    funcdef = ast.parse(source).body[0]
    body = funcdef.body
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


def test_public_methods_under_15_body_lines() -> None:
    func = TenantOwnerRepository.list_active_owner_user_ids
    assert _body_line_count(func) <= 15, (
        f"{func.__qualname__} body too long: {_body_line_count(func)} lines"
    )
