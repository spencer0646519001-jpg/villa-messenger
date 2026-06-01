"""
Production loaders for InquiryService's tenant_pricing_loader and
tenant_special_dates_loader hooks.

InquiryService calls these with a tenant_id, but a tenant's pricing and
special-dates live in its per-tenant config JSON, which is keyed by SLUG
(data/tenants/<slug>/config.json). We bridge id -> slug via TenantRepository,
then read the requested block from the config.

These are factory functions: each returns a Callable[[int], dict] closure that
captures the database_path (and config base_dir), so the closure satisfies
InquiryService's loader contract while keeping the wiring out of the service.
"""

from pathlib import Path
from typing import Callable

from app.config_loader import TenantConfigLoadError, load_tenant_config
from app.repositories.tenant_repository import TenantRepository


def _resolve_slug(database_path: str | Path, tenant_id: int) -> str:
    tenant = TenantRepository(database_path).get_by_id(tenant_id)
    if tenant is None:
        raise TenantConfigLoadError(f"No tenant row for id {tenant_id}")
    return tenant["slug"]


def _load_block(database_path: str | Path, tenant_id: int, block: str, base_dir: str | Path) -> dict:
    slug = _resolve_slug(database_path, tenant_id)
    config = load_tenant_config(tenant_slug=slug, base_dir=base_dir)
    return config.get(block) or {}


def make_tenant_pricing_loader(database_path: str | Path, base_dir: str | Path = "data/tenants") -> Callable[[int], dict]:
    def load(tenant_id: int) -> dict:
        return _load_block(database_path, tenant_id, "pricing", base_dir)
    return load


def make_tenant_special_dates_loader(database_path: str | Path, base_dir: str | Path = "data/tenants") -> Callable[[int], dict]:
    def load(tenant_id: int) -> dict:
        return _load_block(database_path, tenant_id, "special_dates", base_dir)
    return load


def make_tenant_stay_policy_loader(database_path: str | Path, base_dir: str | Path = "data/tenants") -> Callable[[int], dict]:
    def load(tenant_id: int) -> dict:
        return _load_block(database_path, tenant_id, "stay_policy", base_dir)
    return load
