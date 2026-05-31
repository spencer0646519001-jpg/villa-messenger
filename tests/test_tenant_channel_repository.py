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
from app.repositories.tenant_channel_repository import TenantChannelRepository
from app.repositories.tenant_repository import TenantRepository


@pytest.fixture
def database_path() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-tenant-channels")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "tenant-channel-tests.db"
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


def test_create_and_get_by_channel_round_trip(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "zhen-villa")
    repository = TenantChannelRepository(database_path)

    repository.create_channel(
        tenant_id=tenant_id,
        platform="line",
        channel_id="Ufaketestchannel0001abcdef",
        channel_name="Zhen Villa LINE",
        access_token_ref="LINE_CHANNEL_TOKEN_ZHEN123",
        channel_secret_ref="LINE_CHANNEL_SECRET_ZHEN123",
    )

    channel = repository.get_by_channel(
        platform="line",
        channel_id="Ufaketestchannel0001abcdef",
    )

    assert channel is not None
    assert channel["tenant_id"] == tenant_id
    assert channel["channel_name"] == "Zhen Villa LINE"
    assert channel["access_token_ref"] == "LINE_CHANNEL_TOKEN_ZHEN123"
    assert channel["channel_secret_ref"] == "LINE_CHANNEL_SECRET_ZHEN123"


def test_get_by_channel_unknown_returns_none(database_path: Path) -> None:
    repository = TenantChannelRepository(database_path)

    assert (
        repository.get_by_channel(platform="line", channel_id="Unosuchchannel")
        is None
    )


def test_get_by_channel_wrong_platform_returns_none(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "zhen-villa")
    repository = TenantChannelRepository(database_path)
    repository.create_channel(
        tenant_id=tenant_id,
        platform="line",
        channel_id="Ufaketestchannel0001abcdef",
    )

    # platform is part of the lookup: same channel_id under a different
    # platform must not resolve.
    assert (
        repository.get_by_channel(
            platform="messenger",
            channel_id="Ufaketestchannel0001abcdef",
        )
        is None
    )


def test_get_by_channel_inactive_returns_none(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "zhen-villa")
    repository = TenantChannelRepository(database_path)
    channel_id = "Ufaketestchannel0001abcdef"
    repository.create_channel(
        tenant_id=tenant_id,
        platform="line",
        channel_id=channel_id,
    )

    # Genuinely deactivate the row (soft delete), then confirm the
    # is_active = 1 filter excludes it.
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE tenant_channels SET is_active = 0 WHERE channel_id = ?",
            (channel_id,),
        )
        connection.commit()

    assert (
        repository.get_by_channel(platform="line", channel_id=channel_id) is None
    )


def test_channels_resolve_to_their_own_tenant(database_path: Path) -> None:
    tenant_a_id = _create_tenant(database_path, "tenant-a")
    tenant_b_id = _create_tenant(database_path, "tenant-b")
    repository = TenantChannelRepository(database_path)

    repository.create_channel(
        tenant_id=tenant_a_id,
        platform="line",
        channel_id="Uchannel000000000000000aaaa",
    )
    repository.create_channel(
        tenant_id=tenant_b_id,
        platform="line",
        channel_id="Uchannel000000000000000bbbb",
    )

    channel_a = repository.get_by_channel(
        platform="line", channel_id="Uchannel000000000000000aaaa"
    )
    channel_b = repository.get_by_channel(
        platform="line", channel_id="Uchannel000000000000000bbbb"
    )

    assert channel_a["tenant_id"] == tenant_a_id
    assert channel_b["tenant_id"] == tenant_b_id


def test_ref_columns_returned_verbatim_without_resolution(
    database_path: Path,
) -> None:
    tenant_id = _create_tenant(database_path, "zhen-villa")
    repository = TenantChannelRepository(database_path)
    # _ref values are env-var NAMES, not secrets. The repo must return them
    # exactly as stored -- it performs no os.environ / secret resolution.
    repository.create_channel(
        tenant_id=tenant_id,
        platform="line",
        channel_id="Ufaketestchannel0001abcdef",
        access_token_ref="LINE_CHANNEL_TOKEN_ZHEN123",
        channel_secret_ref="LINE_CHANNEL_SECRET_ZHEN123",
    )

    channel = repository.get_by_channel(
        platform="line", channel_id="Ufaketestchannel0001abcdef"
    )

    assert channel["access_token_ref"] == "LINE_CHANNEL_TOKEN_ZHEN123"
    assert channel["channel_secret_ref"] == "LINE_CHANNEL_SECRET_ZHEN123"


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
        TenantChannelRepository.create_channel,
        TenantChannelRepository.get_by_channel,
    ],
)
def test_public_methods_under_15_body_lines(func) -> None:
    assert _body_line_count(func) <= 15, (
        f"{func.__qualname__} body too long: {_body_line_count(func)} lines"
    )
