from contextlib import closing
from pathlib import Path

from app.repositories._helpers import _row_to_dict, _utc_now_iso
from app.repositories.sqlite import get_connection


_INSERT_CHANNEL_SQL = """
INSERT INTO tenant_channels (
    tenant_id,
    platform,
    channel_id,
    channel_name,
    access_token_ref,
    channel_secret_ref,
    created_at,
    updated_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_GET_BY_CHANNEL_SQL = """
SELECT *
FROM tenant_channels
WHERE platform = ?
  AND channel_id = ?
  AND is_active = 1
LIMIT 1
"""


class TenantChannelRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def create_channel(
        self,
        *,
        tenant_id: int,
        platform: str,
        channel_id: str | None = None,
        channel_name: str | None = None,
        access_token_ref: str | None = None,
        channel_secret_ref: str | None = None,
    ) -> int:
        """Insert a channel row, return lastrowid. is_active defaults to 1."""
        now = _utc_now_iso()
        with closing(get_connection(self.database_path)) as connection:
            cursor = connection.execute(
                _INSERT_CHANNEL_SQL,
                (
                    tenant_id, platform, channel_id, channel_name,
                    access_token_ref, channel_secret_ref, now, now,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def get_by_channel(
        self,
        *,
        platform: str,
        channel_id: str,
    ) -> dict | None:
        """Resolve an inbound (platform, channel_id) to its channel row.

        Filters is_active = 1 -- deactivated channels do not route traffic.
        Returns the row dict (carrying tenant_id + the _ref strings verbatim)
        or None. The _ref columns are NOT resolved -- that is the caller's job.
        """
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                _GET_BY_CHANNEL_SQL,
                (platform, channel_id),
            ).fetchone()
        return _row_to_dict(row)
