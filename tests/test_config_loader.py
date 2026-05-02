import json
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config_loader import (
    TenantConfigLoadError,
    build_tenant_context,
    load_tenant_config,
    load_tenant_context,
)
from app.tenant_context import TenantContext


@pytest.fixture
def tenant_config_base_dir() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-tests")
    base_dir = parent_dir / str(uuid.uuid4())
    base_dir.mkdir(parents=True)
    try:
        yield base_dir
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)
        try:
            parent_dir.rmdir()
        except OSError:
            pass


def test_load_tenant_config_loads_existing_config() -> None:
    config = load_tenant_config("zhen123-house")

    assert config["slug"] == "zhen123-house"
    assert config["timezone"] == "Asia/Taipei"
    assert config["default_language"] == "zh-TW"
    assert config["emergency_phone"]


def test_load_tenant_context_returns_context() -> None:
    tenant_context = load_tenant_context("zhen123-house")

    assert tenant_context == TenantContext(
        tenant_id=0,
        tenant_slug="zhen123-house",
        timezone="Asia/Taipei",
        default_language="zh-TW",
    )


def test_missing_tenant_file_raises_error(tenant_config_base_dir: Path) -> None:
    with pytest.raises(TenantConfigLoadError, match="not found"):
        load_tenant_config("missing-tenant", base_dir=tenant_config_base_dir)


def test_config_slug_mismatch_raises_error(tenant_config_base_dir: Path) -> None:
    _write_config(
        tenant_config_base_dir,
        "zhen123-house",
        {**_base_config(), "slug": "other-house"},
    )

    with pytest.raises(TenantConfigLoadError, match="slug mismatch"):
        load_tenant_config("zhen123-house", base_dir=tenant_config_base_dir)


def test_missing_required_field_raises_error(tenant_config_base_dir: Path) -> None:
    config = _base_config()
    del config["timezone"]
    _write_config(tenant_config_base_dir, "zhen123-house", config)

    with pytest.raises(TenantConfigLoadError, match="timezone"):
        load_tenant_config("zhen123-house", base_dir=tenant_config_base_dir)


def test_invalid_json_raises_error(tenant_config_base_dir: Path) -> None:
    config_dir = tenant_config_base_dir / "zhen123-house"
    config_dir.mkdir()
    (config_dir / "config.json").write_text("{invalid-json", encoding="utf-8")

    with pytest.raises(TenantConfigLoadError, match="invalid JSON"):
        load_tenant_config("zhen123-house", base_dir=tenant_config_base_dir)


def test_build_tenant_context_uses_loaded_config_shape() -> None:
    tenant_context = build_tenant_context(_base_config())

    assert tenant_context == TenantContext(
        tenant_id=0,
        tenant_slug="zhen123-house",
        timezone="Asia/Taipei",
        default_language="zh-TW",
    )


def _base_config() -> dict:
    return {
        "slug": "zhen123-house",
        "name": "Zhen123 House",
        "timezone": "Asia/Taipei",
        "default_language": "zh-TW",
        "emergency_phone": "0975-639-757",
    }


def _write_config(base_dir: Path, tenant_slug: str, config: dict) -> None:
    config_dir = base_dir / tenant_slug
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(config),
        encoding="utf-8",
    )
