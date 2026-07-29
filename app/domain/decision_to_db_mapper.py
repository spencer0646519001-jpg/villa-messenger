"""
Translate InquiryDecision.log_payload into two DB-row dicts:
- one for `messages` table
- one for `inquiries` table (or None when no inquiry should be saved)

This is the anti-corruption layer between PR7's log_payload (service-layer
naming) and PR3A's table schemas (DB naming). Future schema changes or
log_payload changes only need to touch this mapper.
"""

import json
from dataclasses import dataclass

from app.domain.inquiry_decision import InquiryDecision


@dataclass(frozen=True)
class DbWritePlan:
    """Pair of row dicts to insert. messages_row is always present;
    inquiry_row is None when no inquiry record should be saved."""

    messages_row: dict
    inquiry_row: dict | None


def build_db_write_plan(decision: InquiryDecision) -> DbWritePlan:
    """Map decision onto messages-row + optional inquiry-row.

    - messages_row is ALWAYS produced (every received message gets logged).
    - inquiry_row is produced when decision.parsed_as_inquiry is True,
      regardless of action_taken (off-mode inquiries still get a row).
    - Row keys match the corresponding repository's create_* kwargs exactly.
    - Missing/None log_payload values pass through as None in the row.
    """
    payload = decision.log_payload
    messages_row = _build_messages_row(payload, action_type=decision.action_type)
    if not _should_save_inquiry(decision):
        return DbWritePlan(messages_row=messages_row, inquiry_row=None)
    return DbWritePlan(
        messages_row=messages_row,
        inquiry_row=_build_inquiry_row(payload),
    )


def _should_save_inquiry(decision: InquiryDecision) -> bool:
    return decision.parsed_as_inquiry


def _build_messages_row(payload: dict, *, action_type: str) -> dict:
    return {
        "tenant_id": payload["tenant_id"],
        "platform": payload["platform"],
        "platform_user_id": payload["customer_platform_id"],
        "customer_display_name": payload.get("customer_display_name"),
        "message_text": payload["raw_text"],
        "category": payload["action_taken"],
        "is_night": payload["is_night"],
        "system_state_at_time": payload["system_state_at_time"],
        "is_urgent": payload["action_taken"] == "urgent",
        # "do_nothing" (schedule-off or Layer 1 per-customer pause) is the ONLY
        # action_type where neither the customer nor the owner learned about
        # this message -- see MessageRepository.list_unhandled / the /待回覆
        # command and nightly digest that surface exactly these rows later.
        "handled": action_type != "do_nothing",
        "raw_log_payload": json.dumps(payload, ensure_ascii=False, default=str),
    }


def _build_inquiry_row(payload: dict) -> dict:
    return {
        "tenant_id": payload["tenant_id"],
        "platform": payload["platform"],
        "platform_user_id": payload["customer_platform_id"],
        "inquiry_type": payload["inquiry_intent"],
        "original_message": payload["raw_text"],
        "checkin_date": payload["parsed_checkin"],
        "checkout_date": payload["parsed_checkout"],
        "adult_count": payload["parsed_adult_count"],
        "child_count": payload["parsed_child_count"],
        "infant_count": payload["parsed_infant_count"],
        "pet_count": payload["parsed_pet_count"],
        "estimated_total_price": payload["quoted_total"],
    }
