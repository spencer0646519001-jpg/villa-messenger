import inspect
import json
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config_loader import TenantConfigLoadError
from app.repositories.sqlite import init_db
from app.repositories.tenant_repository import TenantRepository
from app.services.tenant_config_loaders import (
    _load_block,
    _resolve_slug,
    make_tenant_pricing_loader,
    make_tenant_special_dates_loader,
)


@pytest.fixture
def database_path() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-loaders")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "loaders-tests.db"
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
        emergency_phone="0975-639-757",
    )


def _write_config(base_dir: Path, slug: str, *, pricing=..., special_dates=...) -> None:
    config = {
        "slug": slug,
        "name": slug.title(),
        "timezone": "Asia/Taipei",
        "default_language": "zh-TW",
        "emergency_phone": "0975-639-757",
    }
    if pricing is not ...:
        config["pricing"] = pricing
    if special_dates is not ...:
        config["special_dates"] = special_dates
    tenant_dir = base_dir / slug
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")


# ============================================================
# id -> slug BRIDGE
# ============================================================


def test_resolve_slug_bridges_id_to_slug(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "bridge-villa")

    assert _resolve_slug(database_path, tenant_id) == "bridge-villa"


def test_resolve_slug_unknown_id_raises(database_path: Path) -> None:
    with pytest.raises(TenantConfigLoadError):
        _resolve_slug(database_path, 999)


# ============================================================
# BLOCK LOADING
# ============================================================


def test_pricing_loader_returns_pricing_block(database_path: Path, tmp_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "price-villa")
    _write_config(tmp_path, "price-villa", pricing={"base_prices_per_night": {"x": 1}})
    load = make_tenant_pricing_loader(database_path, base_dir=tmp_path)

    assert load(tenant_id) == {"base_prices_per_night": {"x": 1}}


def test_special_dates_loader_returns_special_dates_block(database_path: Path, tmp_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "dates-villa")
    _write_config(tmp_path, "dates-villa", special_dates={"national_holidays": ["2026-01-01"]})
    load = make_tenant_special_dates_loader(database_path, base_dir=tmp_path)

    assert load(tenant_id) == {"national_holidays": ["2026-01-01"]}


def test_missing_block_returns_empty_dict(database_path: Path, tmp_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "bare-villa")
    _write_config(tmp_path, "bare-villa")  # no pricing, no special_dates
    load = make_tenant_pricing_loader(database_path, base_dir=tmp_path)

    assert load(tenant_id) == {}


def test_loader_unknown_tenant_raises(database_path: Path, tmp_path: Path) -> None:
    load = make_tenant_pricing_loader(database_path, base_dir=tmp_path)

    with pytest.raises(TenantConfigLoadError):
        load(404)


# ============================================================
# REAL CONFIG (the sandbox test's whole point: real prices)
# ============================================================


def test_loads_real_zhen123_pricing(database_path: Path) -> None:
    """Against the REAL data/tenants config -- guards the owner's actual rates."""
    tenant_id = _create_tenant(database_path, "zhen123-house")
    pricing = make_tenant_pricing_loader(database_path)(tenant_id)

    base = pricing["base_prices_per_night"]
    assert base["8_people"]["weekday"] == 9000
    assert base["12_people"]["spring_festival"] == 31000


def test_loads_real_zhen123_special_dates(database_path: Path) -> None:
    tenant_id = _create_tenant(database_path, "zhen123-house")
    special = make_tenant_special_dates_loader(database_path)(tenant_id)

    assert "2026-02-14" in special["spring_festival"]
    assert "2026-01-01" in special["national_holidays"]


# ============================================================
# METHOD-LENGTH DISCIPLINE
# ============================================================


def _body_line_count(func) -> int:
    src = inspect.getsource(func)
    lines = [line for line in src.splitlines()[1:] if line.strip() and not line.strip().startswith("#")]
    return len(lines)


@pytest.mark.parametrize(
    "func",
    [
        _resolve_slug,
        _load_block,
        make_tenant_pricing_loader,
        make_tenant_special_dates_loader,
    ],
)
def test_methods_under_15_body_lines(func) -> None:
    assert _body_line_count(func) <= 15, (
        f"{func.__qualname__} body too long: {_body_line_count(func)} lines"
    )
