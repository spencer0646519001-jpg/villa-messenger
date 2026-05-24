import json
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config_loader import (
    TenantConfigLoadError,
    build_tenant_context,
    load_google_calendar_settings,
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


# ============================================================
# GOOGLE CALENDAR SETTINGS
# ============================================================


def _gc_block(**overrides) -> dict:
    block = {
        "v1_5_enabled": True,
        "booking_keywords": ["枕"],
        "calendar_id_env_var": "TEST_GC_CAL_ID",
        "credentials_env_var": "TEST_GC_CREDS",
    }
    block.update(overrides)
    return block


def test_load_google_calendar_settings_returns_none_when_block_missing() -> None:
    settings = load_google_calendar_settings(_base_config())
    assert settings is None


def test_load_google_calendar_settings_returns_none_when_v1_5_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Env vars set so we can confirm the disabled gate short-circuits BEFORE
    # they are read — disabled means no resolution attempt at all.
    monkeypatch.setenv("TEST_GC_CAL_ID", "should-not-be-read")
    monkeypatch.setenv("TEST_GC_CREDS", "should-not-be-read")
    config = {**_base_config(), "google_calendar": _gc_block(v1_5_enabled=False)}

    assert load_google_calendar_settings(config) is None


def test_load_google_calendar_settings_resolves_env_vars_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_GC_CAL_ID", "abc123@group.calendar.google.com")
    monkeypatch.setenv("TEST_GC_CREDS", "secrets/service-account.json")
    config = {**_base_config(), "google_calendar": _gc_block()}

    settings = load_google_calendar_settings(config)

    assert settings == {
        "enabled": True,
        "calendar_id": "abc123@group.calendar.google.com",
        "credentials_path": "secrets/service-account.json",
        "booking_keywords": ["枕"],
    }


def test_load_google_calendar_settings_missing_env_var_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEST_GC_CAL_ID", raising=False)
    monkeypatch.setenv("TEST_GC_CREDS", "secrets/service-account.json")
    config = {**_base_config(), "google_calendar": _gc_block()}

    with pytest.raises(TenantConfigLoadError, match="TEST_GC_CAL_ID"):
        load_google_calendar_settings(config)


def test_load_google_calendar_settings_missing_credentials_env_var_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_GC_CAL_ID", "abc123@group.calendar.google.com")
    monkeypatch.delenv("TEST_GC_CREDS", raising=False)
    config = {**_base_config(), "google_calendar": _gc_block()}

    with pytest.raises(TenantConfigLoadError, match="TEST_GC_CREDS"):
        load_google_calendar_settings(config)


def test_load_google_calendar_settings_missing_env_var_name_in_block_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = _gc_block()
    del block["calendar_id_env_var"]
    config = {**_base_config(), "google_calendar": block}

    with pytest.raises(TenantConfigLoadError, match="calendar_id_env_var"):
        load_google_calendar_settings(config)


def test_load_google_calendar_settings_keywords_copied_so_external_mutation_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_GC_CAL_ID", "abc123@group.calendar.google.com")
    monkeypatch.setenv("TEST_GC_CREDS", "secrets/service-account.json")
    keywords = ["枕"]
    config = {**_base_config(), "google_calendar": _gc_block(booking_keywords=keywords)}

    settings = load_google_calendar_settings(config)
    keywords.append("妃")  # would be a config-corruption bug

    assert settings["booking_keywords"] == ["枕"]


def test_real_zhen123_config_has_single_booking_keyword() -> None:
    # Locks in the PR8.5b decision: single "枕" keyword (confirmed against
    # real calendar to match every booking title and exclude uncle's "妃").
    config = load_tenant_config("zhen123-house")

    assert config["google_calendar"]["booking_keywords"] == ["枕"]


def _write_config(base_dir: Path, tenant_slug: str, config: dict) -> None:
    config_dir = base_dir / tenant_slug
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(config),
        encoding="utf-8",
    )
