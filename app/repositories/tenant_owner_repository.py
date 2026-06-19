from contextlib import closing
from pathlib import Path

from app.repositories.sqlite import get_connection


_LIST_ACTIVE_OWNER_USER_IDS_SQL = """
SELECT platform_user_id
FROM tenant_owners
WHERE tenant_id = ?
  AND platform = ?
  AND role = 'owner'
  AND is_active = 1
ORDER BY id
"""


class TenantOwnerRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def list_active_owner_user_ids(self, *, tenant_id: int, platform: str) -> list[str]:
        with closing(get_connection(self.database_path)) as connection:
            rows = connection.execute(
                _LIST_ACTIVE_OWNER_USER_IDS_SQL,
                (tenant_id, platform),
            ).fetchall()
        return [str(row["platform_user_id"]) for row in rows]
