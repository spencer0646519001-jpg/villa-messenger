from fastapi import APIRouter, HTTPException, status

from app.config_loader import TenantConfigLoadError, load_tenant_context
from app.settings import settings

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
    }


@router.get("/health/tenant/{tenant_slug}")
def tenant_health(tenant_slug: str) -> dict[str, str]:
    try:
        tenant_context = load_tenant_context(tenant_slug)
    except TenantConfigLoadError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant config could not be loaded.",
        ) from exc

    return {
        "status": "ok",
        "tenant_slug": tenant_context.tenant_slug,
        "timezone": tenant_context.timezone,
        "default_language": tenant_context.default_language,
    }
