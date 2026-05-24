import json
import os
from json import JSONDecodeError
from pathlib import Path

from app.tenant_context import TenantContext


class TenantConfigLoadError(Exception):
    """Raised when a tenant config cannot be loaded or validated."""


REQUIRED_FIELDS = (
    "slug",
    "name",
    "timezone",
    "default_language",
    "emergency_phone",
)


def load_tenant_config(tenant_slug: str, base_dir: str | Path = "data/tenants") -> dict:
    config_path = Path(base_dir) / tenant_slug / "config.json"

    if not config_path.exists():
        raise TenantConfigLoadError(
            f"Tenant config file not found for tenant '{tenant_slug}': {config_path}"
        )

    try:
        with config_path.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
    except JSONDecodeError as exc:
        raise TenantConfigLoadError(
            f"Tenant config file contains invalid JSON for tenant '{tenant_slug}': {config_path}"
        ) from exc

    if not isinstance(config, dict):
        raise TenantConfigLoadError(
            f"Tenant config must be a JSON object for tenant '{tenant_slug}': {config_path}"
        )

    _validate_required_fields(config, tenant_slug)

    config_slug = config["slug"]
    if config_slug != tenant_slug:
        raise TenantConfigLoadError(
            f"Tenant config slug mismatch: expected '{tenant_slug}', got '{config_slug}'"
        )

    return config


def build_tenant_context(config: dict) -> TenantContext:
    tenant_slug = str(config.get("slug", "<unknown>"))
    _validate_required_fields(config, tenant_slug)

    return TenantContext(
        tenant_id=0,
        tenant_slug=config["slug"],
        timezone=config["timezone"],
        default_language=config["default_language"],
    )


def load_tenant_context(
    tenant_slug: str, base_dir: str | Path = "data/tenants"
) -> TenantContext:
    config = load_tenant_config(tenant_slug=tenant_slug, base_dir=base_dir)
    return build_tenant_context(config)


def _validate_required_fields(config: dict, tenant_slug: str) -> None:
    missing_fields = [field for field in REQUIRED_FIELDS if field not in config]
    if missing_fields:
        fields = ", ".join(missing_fields)
        raise TenantConfigLoadError(
            f"Tenant config for tenant '{tenant_slug}' is missing required field(s): {fields}"
        )


def load_google_calendar_settings(config: dict) -> dict | None:
    """Resolve the google_calendar block into runtime settings.

    Returns None when the block is missing or v1_5_enabled is False — the
    caller treats this as 'calendar gating disabled'. When enabled, the
    calendar_id and credentials path are resolved from the env vars named
    in the config; missing env vars raise TenantConfigLoadError so a broken
    deployment surfaces at config-load time rather than at first call.
    """
    block = config.get("google_calendar")
    if not block or not block.get("v1_5_enabled"):
        return None
    calendar_id = _require_env(block, "calendar_id_env_var")
    credentials_path = _require_env(block, "credentials_env_var")
    return {
        "enabled": True,
        "calendar_id": calendar_id,
        "credentials_path": credentials_path,
        "booking_keywords": list(block.get("booking_keywords", [])),
    }


def _require_env(block: dict, env_var_key: str) -> str:
    env_var_name = block.get(env_var_key)
    if not env_var_name:
        raise TenantConfigLoadError(
            f"google_calendar block missing '{env_var_key}'"
        )
    value = os.environ.get(env_var_name)
    if not value:
        raise TenantConfigLoadError(
            f"google_calendar env var '{env_var_name}' is not set"
        )
    return value
