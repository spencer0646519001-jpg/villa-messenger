from contextlib import closing
from pathlib import Path

from app.repositories._helpers import _row_to_dict, _utc_now_iso
from app.repositories.sqlite import get_connection


class TenantRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def create_tenant(
        self,
        slug: str,
        name: str,
        timezone: str,
        default_language: str,
        emergency_phone: str | None = None,
    ) -> int:
        now = _utc_now_iso()

        with closing(get_connection(self.database_path)) as connection:
            cursor = connection.execute(
                """
                INSERT INTO tenants (
                    slug,
                    name,
                    timezone,
                    default_language,
                    emergency_phone,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug,
                    name,
                    timezone,
                    default_language,
                    emergency_phone,
                    now,
                    now,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def get_by_slug(self, slug: str) -> dict | None:
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                "SELECT * FROM tenants WHERE slug = ? LIMIT 1",
                (slug,),
            ).fetchone()

        return _row_to_dict(row)

    def list_active(self) -> list[dict]:
        with closing(get_connection(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT * FROM tenants WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_by_id(self, tenant_id: int) -> dict | None:
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                "SELECT * FROM tenants WHERE id = ? LIMIT 1",
                (tenant_id,),
            ).fetchone()

        return _row_to_dict(row)
