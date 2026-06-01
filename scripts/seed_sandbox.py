"""
Seed the LOCAL live-sandbox SQLite DB so the LINE webhook can resolve the test
channel to a tenant. Idempotent and reversible (the DB file is gitignored;
delete data/homestay.db to start over).

Reuses TenantRepository / TenantChannelRepository -- no hand-written SQL -- so
this stays consistent with the code the webhook itself runs.

Two phases, driven by whether a destination arg is supplied:

  python scripts/seed_sandbox.py
      -> init_db + seed tenant only. Channel deferred (we don't know the bot's
         `destination` until a webhook arrives and the handler logs it as
         "unknown channel (channel_id=...)").

  python scripts/seed_sandbox.py <destination>
      -> also seed the line channel, mapping <destination> -> zhen123-house.

`destination` is the LINE bot's own user id (a `U...` string), NOT the numeric
provider channel id. Capture it from the webhook's unknown-channel log line.
"""

import sys

from app.repositories.sqlite import init_db
from app.repositories.tenant_channel_repository import TenantChannelRepository
from app.repositories.tenant_repository import TenantRepository

# NOTE: must match app.settings.database_path. Hardcoded for now; unify to
# `from app.settings import settings` in a future tidy. If settings changes
# the DB path, update this too or the seed writes to a file the app won't read.
DATABASE_PATH = "data/homestay.db"

TENANT_SLUG = "zhen123-house"
TENANT_NAME = "枕123"
TENANT_TIMEZONE = "Asia/Taipei"
TENANT_LANGUAGE = "zh-TW"

CHANNEL_PLATFORM = "line"
CHANNEL_SECRET_REF = "LINE_TEST_CHANNEL_SECRET"


def _seed_tenant(tenants: TenantRepository) -> int:
    """Insert the sandbox tenant if absent. Returns its id either way."""
    existing = tenants.get_by_slug(TENANT_SLUG)
    if existing is not None:
        print(f"tenant: already exists (slug={TENANT_SLUG}, id={existing['id']})")
        return int(existing["id"])
    tenant_id = tenants.create_tenant(
        slug=TENANT_SLUG,
        name=TENANT_NAME,
        timezone=TENANT_TIMEZONE,
        default_language=TENANT_LANGUAGE,
    )
    print(f"tenant: CREATED (slug={TENANT_SLUG}, id={tenant_id})")
    return tenant_id


def _seed_channel(channels: TenantChannelRepository, tenant_id: int, destination: str) -> None:
    """Insert the line channel mapping destination -> tenant, if absent."""
    existing = channels.get_by_channel(platform=CHANNEL_PLATFORM, channel_id=destination)
    if existing is not None:
        print(f"channel: already exists (channel_id={destination}, id={existing['id']})")
        return
    channel_id = channels.create_channel(
        tenant_id=tenant_id,
        platform=CHANNEL_PLATFORM,
        channel_id=destination,
        channel_secret_ref=CHANNEL_SECRET_REF,
    )
    print(
        f"channel: CREATED (channel_id={destination}, "
        f"secret_ref={CHANNEL_SECRET_REF}, tenant_id={tenant_id}, id={channel_id})"
    )


def main() -> None:
    destination = sys.argv[1] if len(sys.argv) > 1 else None

    init_db(DATABASE_PATH)
    print(f"db: init_db ok ({DATABASE_PATH})")

    tenant_id = _seed_tenant(TenantRepository(DATABASE_PATH))

    if destination is None:
        print("tenant seeded, channel deferred (no destination arg)")
        return

    _seed_channel(TenantChannelRepository(DATABASE_PATH), tenant_id, destination)


if __name__ == "__main__":
    # Chinese tenant name -> force UTF-8 stdout so printing doesn't crash on a
    # cp950/legacy Windows console.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    main()
