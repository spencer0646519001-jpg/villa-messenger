from dataclasses import dataclass


@dataclass(frozen=True)
class TenantContext:
    tenant_id: int
    tenant_slug: str
    timezone: str
    default_language: str

