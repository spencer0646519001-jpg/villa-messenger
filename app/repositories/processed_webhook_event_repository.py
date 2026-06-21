from contextlib import closing
from pathlib import Path

from app.repositories._helpers import _utc_now_iso
from app.repositories.sqlite import get_connection


_MARK_IF_NEW_SQL = """
INSERT INTO processed_webhook_events (
    tenant_id,
    webhook_event_id,
    created_at
)
VALUES (?, ?, ?)
ON CONFLICT(tenant_id, webhook_event_id) DO NOTHING
"""


class ProcessedWebhookEventRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def mark_if_new(self, *, tenant_id: int, webhook_event_id: str) -> bool:
        with closing(get_connection(self.database_path)) as connection:
            cursor = connection.execute(
                _MARK_IF_NEW_SQL,
                (tenant_id, webhook_event_id, _utc_now_iso()),
            )
            connection.commit()
        return cursor.rowcount == 1
