import sqlite3
from contextlib import closing
from pathlib import Path

from app.repositories._helpers import _row_to_dict, _utc_now_iso
from app.repositories.sqlite import get_connection


_INSERT_INQUIRY_SQL = """
INSERT INTO inquiries (
    tenant_id,
    contact_id,
    message_id,
    platform,
    platform_user_id,
    checkin_date,
    checkout_date,
    nights,
    adult_count,
    child_count,
    infant_count,
    guest_count,
    has_pet,
    pet_count,
    pet_type,
    pet_fee_per_pet,
    pet_fee_total,
    needs_pet_count_confirmation,
    inquiry_type,
    estimated_lodging_price,
    long_stay_discount,
    estimated_total_price,
    price_basis,
    availability_status,
    status,
    original_message,
    reply_text,
    needs_owner_confirmation,
    created_at
)
VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""


class InquiryRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def create_inquiry(
        self,
        tenant_id: int,
        platform: str,
        platform_user_id: str,
        inquiry_type: str,
        original_message: str,
        contact_id: int | None = None,
        message_id: int | None = None,
        checkin_date: str | None = None,
        checkout_date: str | None = None,
        nights: int | None = None,
        adult_count: int | None = None,
        child_count: int | None = None,
        infant_count: int | None = None,
        guest_count: int | None = None,
        has_pet: bool = False,
        pet_count: int | None = None,
        pet_type: str | None = None,
        pet_fee_per_pet: int | None = None,
        pet_fee_total: int | None = None,
        needs_pet_count_confirmation: bool = False,
        estimated_lodging_price: int | None = None,
        long_stay_discount: int | None = None,
        estimated_total_price: int | None = None,
        price_basis: str | None = None,
        availability_status: str = "needs_manual_confirmation",
        status: str = "new",
        reply_text: str | None = None,
        needs_owner_confirmation: bool = True,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        params = (
            tenant_id,
            contact_id,
            message_id,
            platform,
            platform_user_id,
            checkin_date,
            checkout_date,
            nights,
            adult_count,
            child_count,
            infant_count,
            guest_count,
            int(has_pet),
            pet_count,
            pet_type,
            pet_fee_per_pet,
            pet_fee_total,
            int(needs_pet_count_confirmation),
            inquiry_type,
            estimated_lodging_price,
            long_stay_discount,
            estimated_total_price,
            price_basis,
            availability_status,
            status,
            original_message,
            reply_text,
            int(needs_owner_confirmation),
            _utc_now_iso(),
        )
        if connection is not None:
            return self._insert_inquiry(connection, params)
        with closing(get_connection(self.database_path)) as own_connection:
            row_id = self._insert_inquiry(own_connection, params)
            own_connection.commit()
            return row_id

    def _insert_inquiry(
        self,
        connection: sqlite3.Connection,
        params: tuple,
    ) -> int:
        cursor = connection.execute(_INSERT_INQUIRY_SQL, params)
        return int(cursor.lastrowid)

    def get_by_id(self, tenant_id: int, inquiry_id: int) -> dict | None:
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM inquiries
                WHERE tenant_id = ?
                  AND id = ?
                LIMIT 1
                """,
                (tenant_id, inquiry_id),
            ).fetchone()

        return _row_to_dict(row)

    def list_open(self, tenant_id: int, limit: int = 20) -> list[dict]:
        with closing(get_connection(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM inquiries
                WHERE tenant_id = ?
                  AND status != 'closed'
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (tenant_id, limit),
            ).fetchall()

        return [dict(row) for row in rows]
