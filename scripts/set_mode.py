"""
Flip the LOCAL live-sandbox tenant's operation mode on/off (or inspect it) so
the LINE webhook will actually compose a customer reply during a manual test.

Companion to scripts/seed_sandbox.py. Reuses TenantRepository.get_by_slug +
OperationModeService -- no hand-written SQL -- so the override it writes is
exactly what the webhook reads back.

Usage (run from repo root so `app` is importable):

  python scripts/set_mode.py on       -> manual override ON, prints mode + expiry
  python scripts/set_mode.py off      -> manual override OFF, prints mode + expiry
  python scripts/set_mode.py status   -> prints current effective mode + expiry

The manual override is NOT permanent: turn_on/turn_off write a manual_valid_until
that expires at the next auto-schedule boundary (default window 23:00->08:00, so
a daytime `on` lasts until 23:00 tenant-local), after which the auto schedule
resumes. Re-run `on` to extend.
"""

import sys

from app.repositories.operation_state_repository import OperationStateRepository
from app.repositories.tenant_repository import TenantRepository
from app.services.operation_mode_service import OperationModeService

# NOTE: must match app.settings.database_path. Hardcoded for now; unify to
# `from app.settings import settings` in a future tidy. If settings changes
# the DB path, update this too or this writes to a file the app won't read.
DATABASE_PATH = "data/homestay.db"

TENANT_SLUG = "zhen123-house"
TENANT_TIMEZONE = "Asia/Taipei"

_USAGE = "usage: python scripts/set_mode.py [on|off|status]"


def _report(service: OperationModeService, tenant_id: int) -> None:
    """Print effective mode + the persisted manual_valid_until."""
    active = service.is_system_active(
        tenant_id=tenant_id, tenant_timezone=TENANT_TIMEZONE
    )
    row = OperationStateRepository(DATABASE_PATH).get_or_create(tenant_id)
    print(f"effective mode: {'on' if active else 'off'}")
    print(f"manual_mode:    {row.get('manual_mode')}")
    print(f"valid_until:    {row.get('manual_valid_until')}")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else None
    if action not in {"on", "off", "status"}:
        print(_USAGE)
        sys.exit(2)

    tenant = TenantRepository(DATABASE_PATH).get_by_slug(TENANT_SLUG)
    if tenant is None:
        print(f"tenant not found (slug={TENANT_SLUG}); run seed_sandbox.py first")
        sys.exit(1)
    tenant_id = int(tenant["id"])

    service = OperationModeService(repo=OperationStateRepository(DATABASE_PATH))
    if action == "on":
        service.turn_on(tenant_id=tenant_id, tenant_timezone=TENANT_TIMEZONE)
    elif action == "off":
        service.turn_off(tenant_id=tenant_id, tenant_timezone=TENANT_TIMEZONE)

    _report(service, tenant_id)


if __name__ == "__main__":
    # Match seed_sandbox.py: force UTF-8 stdout so printing doesn't crash on a
    # cp950/legacy Windows console.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    main()
