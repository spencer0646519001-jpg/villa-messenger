"""
Normalizes a real pipeline outcome (InquiryDecision + ComposedReply) into the same
action vocabulary the gold set already uses (`gold.expected_action`), so scoring is a
plain string equality instead of exact-reply-text comparison.

classify_actual_action() is an ORDERED, deterministic classifier: every branch is an
exact-equality or fixed-substring check against constants imported directly from
app.domain.reply_text -- the single source of truth for customer-facing wording. No
fuzzy/similarity matching. Order matters (most specific checks first); anything that
matches nothing falls through to "unknown", which the report surfaces as a hard
failure rather than silently mis-scoring.
"""

from __future__ import annotations

from app.domain.inquiry_decision import InquiryDecision
from app.domain.reply_text import (
    DATE_RANGE_CLARIFICATION_MESSAGE,
    FAQ_AMENITIES_EMPTY,
    FAQ_AMENITIES_HEADER,
    FAQ_BREAKFAST_NOT_PROVIDED,
    FAQ_BREAKFAST_PROVIDED,
    FAQ_DEFER_CLOSE,
    FAQ_FALLBACK_LEAD,
    FAQ_LOCATION_EMPTY,
    FAQ_LOCATION_PREFIX,
    FAQ_NOTIFIED_CLOSE,
    FAQ_PARKING_AVAILABLE,
    FAQ_PARKING_AVAILABLE_FREE,
    FAQ_PARKING_NOT_AVAILABLE,
    FAQ_PETS_NOT_ALLOWED,
    FAQ_ROOM_TYPE_EMPTY,
    FAQ_WHOLE_HOUSE,
    FAQ_WIFI_NOT_PROVIDED,
    FAQ_WIFI_PROVIDED,
    FAQ_WIFI_PROVIDED_FREE,
    FULL_HOUSE_MESSAGE,
    INVALID_DATE_MESSAGE,
    MANUAL_REVIEW_MESSAGE,
    MISSING_INFO_HEADER,
    MISSING_ROOM_COUNT_MESSAGE,
    OVER_CAPACITY_MESSAGE,
    QUOTE_GREETING,
    RECONFIRM_STALE_CONTEXT_MESSAGE,
    SINGLE_MISSING_CHECKIN_MESSAGE,
    SINGLE_MISSING_CHECKOUT_MESSAGE,
    SINGLE_MISSING_GUEST_COUNT_MESSAGE,
    SINGLE_MISSING_PET_COUNT_MESSAGE,
)
from app.services.conversation_reply_composer import ComposedReply

# Stable substrings for the parametrized/dynamic templates (checkout time, deposit,
# room type, pets policy, bbq, room-capacity suggestion, assumed-single-night probe).
# Each is the fixed Chinese wording that survives regardless of the template's
# {parameters} -- see app/domain/reply_templates.py for the full render_* functions.
_ROOM_CAPACITY_SUGGESTION_MARKER = "間房可能住不下喔"
_ASSUMED_SINGLE_NIGHT_MARKER = "住一晚)目前可能已有訂房"
_FAQ_CHECKOUT_MARKER = "之後,退房時間為"
_FAQ_PETS_ALLOWED_MARKER = "我們可以接受寵物入住"
_FAQ_BBQ_MARKER = "烤肉需事先預約,清潔費"
_FAQ_DEPOSIT_MARKER = "訂金為房價三成"

_FAQ_MARKERS: tuple[str, ...] = (
    FAQ_BREAKFAST_PROVIDED,
    FAQ_BREAKFAST_NOT_PROVIDED,
    FAQ_PETS_NOT_ALLOWED,
    _FAQ_PETS_ALLOWED_MARKER,
    FAQ_WIFI_PROVIDED_FREE,
    FAQ_WIFI_PROVIDED,
    FAQ_WIFI_NOT_PROVIDED,
    FAQ_PARKING_AVAILABLE_FREE,
    FAQ_PARKING_AVAILABLE,
    FAQ_PARKING_NOT_AVAILABLE,
    FAQ_WHOLE_HOUSE,
    FAQ_AMENITIES_HEADER,
    FAQ_AMENITIES_EMPTY,
    FAQ_ROOM_TYPE_EMPTY,
    FAQ_LOCATION_PREFIX,
    FAQ_LOCATION_EMPTY,
    _FAQ_CHECKOUT_MARKER,
    _FAQ_BBQ_MARKER,
    _FAQ_DEPOSIT_MARKER,
    FAQ_FALLBACK_LEAD,
    FAQ_NOTIFIED_CLOSE,
    FAQ_DEFER_CLOSE,
)

_SINGLE_MISSING_MESSAGES: frozenset[str] = frozenset(
    {
        SINGLE_MISSING_CHECKIN_MESSAGE,
        SINGLE_MISSING_CHECKOUT_MESSAGE,
        SINGLE_MISSING_GUEST_COUNT_MESSAGE,
        SINGLE_MISSING_PET_COUNT_MESSAGE,
    }
)

# Gold's one compound label doesn't correspond to a single ComposedReply -- the state
# only ever shows ONE reply per turn. The scenario it names (stale-off reconfirm nudge
# fires now; missing_room_count would fire on the customer's NEXT reply) is fully
# satisfied by the actual "stale_context_reconfirmation" turn. See eval plan decision 3
# and action_taxonomy tests for the alias check.
ACTION_ALIASES: dict[str, str] = {
    "stale_context_reconfirm_then_missing_room_count": "stale_context_reconfirmation",
}

UNKNOWN = "unknown"


def classify_actual_action(
    decision: InquiryDecision, composed: ComposedReply, state: dict | None
) -> str:
    text = composed.text
    if decision.was_urgent:
        return "urgent_push_owner"
    if decision.was_system_off:
        return "off_mode_logged_only"
    if text == RECONFIRM_STALE_CONTEXT_MESSAGE:
        return "stale_context_reconfirmation"
    if text is not None and _contains_any(text, _FAQ_MARKERS):
        return "faq"
    if text == MISSING_ROOM_COUNT_MESSAGE:
        return "missing_room_count"
    if text is not None and _ROOM_CAPACITY_SUGGESTION_MARKER in text:
        return "room_capacity_suggestion"
    if text == MANUAL_REVIEW_MESSAGE:
        return "room_manual_review"
    if text == OVER_CAPACITY_MESSAGE:
        return "over_capacity"
    if text == INVALID_DATE_MESSAGE:
        return "invalid_date"
    if text is not None and _ASSUMED_SINGLE_NIGHT_MARKER in text:
        return "assumed_single_night_full_house"
    if text == FULL_HOUSE_MESSAGE:
        return "full_house"
    if text == DATE_RANGE_CLARIFICATION_MESSAGE:
        return "ask_date_range_clarification"
    if text in _SINGLE_MISSING_MESSAGES or (text is not None and text.startswith(MISSING_INFO_HEADER)):
        return "missing_info"
    if text is not None and text.startswith(QUOTE_GREETING):
        return "quoted_unverified" if composed.owner_push_text is not None else "quoted"
    if decision.log_payload.get("action_taken") == "non_inquiry_uncategorized":
        return "non_inquiry_uncategorized"
    if text is None and composed.owner_push_text is None:
        return "no_reply"
    return UNKNOWN


def actions_match(actual: str, expected: str) -> bool:
    """gold's expected_action vs. our normalized actual action, with the one
    documented compound-label alias resolved in gold's favor."""
    return actual == expected or actual == ACTION_ALIASES.get(expected)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)
