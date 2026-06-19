"""
Add or inspect the LOCAL live-sandbox tenant's LINE owner user id.

Companion to scripts/set_mode.py and scripts/seed_sandbox.py. This is a manual
seed helper for real-device testing of owner commands.

Usage (run from repo root so `app` is importable):

  python scripts/add_owner.py add <platform_user_id> [display_name]
  python scripts/add_owner.py list
"""

from contextlib import closing
from datetime import datetime, timezone
import sys

from app.repositories.sqlite import get_connection
from app.repositories.tenant_repository import TenantRepository

# NOTE: must match app.settings.database_path. Hardcoded for now; unify to
# `from app.settings import settings` in a future tidy. If settings changes
# the DB path, update this too or this writes to a file the app won't read.
DATABASE_PATH = "data/homestay.db"

TENANT_SLUG = "zhen123-house"
PLATFORM = "line"

_USAGE = (
    "usage: python scripts/add_owner.py add <platform_user_id> [display_name]\n"
    "       python scripts/add_owner.py list"
)

_UPSERT_OWNER_SQL = """
INSERT INTO tenant_owners (
    tenant_id,
    platform,
    platform_user_id,
    display_name,
    role,
    is_active,
    created_at,
    updated_at
)
VALUES (?, ?, ?, ?, 'owner', 1, ?, ?)
ON CONFLICT(tenant_id, platform, platform_user_id) DO UPDATE SET
    display_name = excluded.display_name,
    role = 'owner',
    is_active = 1,
    updated_at = excluded.updated_at
"""

_LIST_OWNERS_SQL = """
SELECT
    id,
    tenant_id,
    platform,
    platform_user_id,
    display_name,
    role,
    is_active,
    created_at,
    updated_at
FROM tenant_owners
WHERE tenant_id = ?
  AND platform = ?
ORDER BY id
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tenant_id() -> int:
    tenant = TenantRepository(DATABASE_PATH).get_by_slug(TENANT_SLUG)
    if tenant is None:
        print(f"tenant not found (slug={TENANT_SLUG}); run seed_sandbox.py first")
        sys.exit(1)
    return int(tenant["id"])


def _upsert_owner(tenant_id: int, platform_user_id: str, display_name: str | None) -> None:
    now = _utc_now_iso()
    with closing(get_connection(DATABASE_PATH)) as connection:
        connection.execute(
            _UPSERT_OWNER_SQL,
            (
                tenant_id,
                PLATFORM,
                platform_user_id,
                display_name,
                now,
                now,
            ),
        )
        connection.commit()


def _list_owners(tenant_id: int) -> None:
    with closing(get_connection(DATABASE_PATH)) as connection:
        rows = connection.execute(_LIST_OWNERS_SQL, (tenant_id, PLATFORM)).fetchall()

    print(f"owners (tenant_id={tenant_id}, platform={PLATFORM}):")
    if not rows:
        print("(none)")
        return

    for row in rows:
        print(
            "id={id} user_id={platform_user_id} display_name={display_name} "
            "role={role} is_active={is_active} created_at={created_at} "
            "updated_at={updated_at}".format(**dict(row))
        )


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else None
    if action == "add":
        if len(sys.argv) not in {3, 4}:
            print(_USAGE)
            sys.exit(2)
        platform_user_id = sys.argv[2]
        display_name = sys.argv[3] if len(sys.argv) == 4 else None

        tenant_id = _tenant_id()
        _upsert_owner(tenant_id, platform_user_id, display_name)
        _list_owners(tenant_id)
        return

    if action == "list":
        if len(sys.argv) != 2:
            print(_USAGE)
            sys.exit(2)

        tenant_id = _tenant_id()
        _list_owners(tenant_id)
        return

    print(_USAGE)
    sys.exit(2)


if __name__ == "__main__":
    # Match seed_sandbox.py: force UTF-8 stdout so printing doesn't crash on a
    # cp950/legacy Windows console.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    main()
