"""
MessagePersistenceService: persist an InquiryDecision to SQLite.

Wraps message + (optional) inquiry inserts in a single transaction so partial
writes are impossible. Connection management follows the existing repository
pattern: takes a database_path, opens connections per call. The mapper
(decision_to_db_mapper) handles the log_payload → row translation; this
service only orchestrates the transactional write.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

from app.domain.decision_to_db_mapper import build_db_write_plan
from app.domain.inquiry_decision import InquiryDecision
from app.repositories.inquiry_repository import InquiryRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.sqlite import get_connection


class MessagePersistenceService:
    def __init__(self, *, database_path: str | Path) -> None:
        self._database_path = database_path
        self._messages = MessageRepository(database_path)
        self._inquiries = InquiryRepository(database_path)

    def persist(self, *, decision: InquiryDecision) -> dict:
        """Persist decision atomically; returns {message_id, inquiry_id}. Not idempotent."""
        plan = build_db_write_plan(decision)
        with closing(get_connection(self._database_path)) as conn:
            with conn:
                message_id = self._save_message(conn, plan.messages_row)
                inquiry_id = self._save_inquiry_if_present(
                    conn, plan.inquiry_row, message_id
                )
        return {"message_id": message_id, "inquiry_id": inquiry_id}

    def _save_message(self, conn: sqlite3.Connection, row: dict) -> int:
        return self._messages.create_message(connection=conn, **row)

    def _save_inquiry_if_present(
        self,
        conn: sqlite3.Connection,
        row: dict | None,
        message_id: int,
    ) -> int | None:
        if row is None:
            return None
        return self._inquiries.create_inquiry(
            connection=conn, message_id=message_id, **row
        )
