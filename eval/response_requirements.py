"""
Deterministic checkers for gold's `response_requirements.must_include` /
`must_not_claim` tags (task section 3D).

Two tag shapes appear in the frozen gold set:
  - literal tags, e.g. "ask_room_count"
  - parametrized tags, e.g. "disclose_assumed_one_night_range(8/10-8/11)" --
    `parse_tag()` splits these into (name, args).

must_include checkers do a substring/equality check against constants imported
directly from app.domain.reply_text (the same constants app.domain.reply_templates
renders from) -- never fuzzy string similarity. must_not_claim checkers are
absence-of-banned-phrase checks: reply_templates.py structurally never emits these
claims, so these are expected to PASS by construction today -- they exist as a
regression tripwire, not because a violation is anticipated.

Any tag with no registered checker returns NOT_DETERMINISTIC rather than being
silently treated as pass or fail (task section 3D: "isolate cases that genuinely
cannot be implemented deterministically").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from app.domain.reply_text import (
    DATE_RANGE_CLARIFICATION_MESSAGE,
    MISSING_CHECKIN_LINE,
    MISSING_CHECKOUT_LINE,
    MISSING_GUEST_COUNT_LINE,
    MISSING_PET_COUNT_LINE,
    MISSING_ROOM_COUNT_MESSAGE,
    RECONFIRM_STALE_CONTEXT_MESSAGE,
    SAFETY_NOTE,
    SINGLE_MISSING_CHECKIN_MESSAGE,
    SINGLE_MISSING_CHECKOUT_MESSAGE,
    SINGLE_MISSING_GUEST_COUNT_MESSAGE,
    SINGLE_MISSING_PET_COUNT_MESSAGE,
)
from app.services.conversation_reply_composer import ComposedReply

NOT_DETERMINISTIC = "NOT_DETERMINISTIC"

_FAQ_BBQ_MARKER = "烤肉需事先預約,清潔費"
_OWNER_CONFIRMATION_MARKERS = (
    SAFETY_NOTE,
    "小孩是否需依實際佔床情況調整",
    "嬰兒是否需依實際佔床情況調整",
    "寵物清潔費為每隻",
    "烤肉清潔費為",
)
_OWNER_COMMAND_TOKENS = (
    "/幫助",
    "/詢價",
    "/今日詢價",
    "/未處理",
    "/緊急",
    "/昨晚總覽",
    "/今天總覽",
    "/綁定",
    "/查訂房",
    "/查客人",
    "/解除綁定",
)


@dataclass(frozen=True)
class ParsedTag:
    name: str
    args: str | None


_TAG_PATTERN = re.compile(r"^([a-z_]+)(?:\(([^)]*)\))?$")


def parse_tag(tag: str) -> ParsedTag:
    match = _TAG_PATTERN.match(tag)
    if not match:
        return ParsedTag(name=tag, args=None)
    return ParsedTag(name=match.group(1), args=match.group(2))


def _text(composed: ComposedReply) -> str:
    return composed.text or ""


def _asks_checkin(composed: ComposedReply) -> bool:
    text = _text(composed)
    return text == SINGLE_MISSING_CHECKIN_MESSAGE or MISSING_CHECKIN_LINE in text


def _asks_checkout(composed: ComposedReply) -> bool:
    text = _text(composed)
    return text == SINGLE_MISSING_CHECKOUT_MESSAGE or MISSING_CHECKOUT_LINE in text


def _asks_guest_count(composed: ComposedReply) -> bool:
    text = _text(composed)
    return text == SINGLE_MISSING_GUEST_COUNT_MESSAGE or MISSING_GUEST_COUNT_LINE in text


def _asks_pet_count(composed: ComposedReply) -> bool:
    text = _text(composed)
    return text == SINGLE_MISSING_PET_COUNT_MESSAGE or MISSING_PET_COUNT_LINE in text


def _asks_room_count(composed: ComposedReply) -> bool:
    return MISSING_ROOM_COUNT_MESSAGE in _text(composed)


def _date_range_clarification(composed: ComposedReply) -> bool:
    return _text(composed) == DATE_RANGE_CLARIFICATION_MESSAGE


def _stale_context_reconfirmation(composed: ComposedReply) -> bool:
    return RECONFIRM_STALE_CONTEXT_MESSAGE in _text(composed)


def _quote_scope_disclaimer(composed: ComposedReply) -> bool:
    return SAFETY_NOTE in _text(composed)


def _bbq_policy_answer(composed: ComposedReply) -> bool:
    return _FAQ_BBQ_MARKER in _text(composed)


def _owner_confirmation(composed: ComposedReply) -> bool:
    text = _text(composed)
    return any(marker in text for marker in _OWNER_CONFIRMATION_MARKERS)


def _disclose_assumed_one_night_range(args: str | None, composed: ComposedReply) -> bool:
    if not args or "-" not in args:
        return False
    checkin, _, checkout = args.partition("-")
    text = _text(composed)
    return checkin.strip() in text and checkout.strip() in text


_ROOM_SUGGESTION_ARGS = re.compile(r"^at_least_(\d+)_rooms_for_(\d+)$")


def _guest_count_aware_minimum_room_suggestion(args: str | None, composed: ComposedReply) -> bool:
    if not args:
        return False
    match = _ROOM_SUGGESTION_ARGS.match(args)
    if not match:
        return False
    suggested_room_count = match.group(1)
    text = _text(composed)
    return f"建議開 {suggested_room_count} 房" in text


# name -> checker(composed) -> bool, for tags with no parenthesized args.
_MUST_INCLUDE_LITERAL: dict[str, Callable[[ComposedReply], bool]] = {
    "quote_scope_disclaimer": _quote_scope_disclaimer,
    "ask_checkin_date": _asks_checkin,
    "ask_checkout_date": _asks_checkout,
    "ask_guest_count": _asks_guest_count,
    "ask_firm_guest_count": _asks_guest_count,
    "ask_pet_count": _asks_pet_count,
    "ask_room_count": _asks_room_count,
    "date_range_clarification": _date_range_clarification,
    "stale_context_reconfirmation": _stale_context_reconfirmation,
    "bbq_policy_answer": _bbq_policy_answer,
    "bbq_information": _bbq_policy_answer,
    "owner_confirmation": _owner_confirmation,
}

# name -> checker(args, composed) -> bool, for tags with parenthesized args.
_MUST_INCLUDE_PARAMETRIZED: dict[str, Callable[[str | None, ComposedReply], bool]] = {
    "disclose_assumed_one_night_range": _disclose_assumed_one_night_range,
    "guest_count_aware_minimum_room_suggestion": _guest_count_aware_minimum_room_suggestion,
}


def check_must_include(tag: str, composed: ComposedReply) -> bool | str:
    """Returns True/False, or NOT_DETERMINISTIC when no checker is registered."""
    parsed = parse_tag(tag)
    if parsed.name in _MUST_INCLUDE_PARAMETRIZED:
        return _MUST_INCLUDE_PARAMETRIZED[parsed.name](parsed.args, composed)
    if parsed.name in _MUST_INCLUDE_LITERAL:
        return _MUST_INCLUDE_LITERAL[parsed.name](composed)
    return NOT_DETERMINISTIC


# Banned-phrase blocklists. reply_templates.py structurally never emits these claims
# (the moat's SAFETY_NOTE / hard_rules in every tenant config forbid it), so these are
# a regression tripwire: expected to pass today, not expected to catch anything yet.
_BANNED_PHRASES: dict[str, tuple[str, ...]] = {
    "guarantee_availability": ("確認有空房", "保證有空房", "已確認空房", "一定有房", "確定有空房"),
    "confirm_booking_confirmed": ("已確認訂房", "訂房已確認", "訂房成立", "已為您訂房", "已完成訂房"),
    "confirm_room_reserved": ("已為您保留", "房間已保留", "已鎖房", "已幫您留房"),
    "process_or_request_payment": ("請付款", "請匯款", "已收訂金", "付款連結", "請匯訂金"),
    "imply_quote_ready": ("報價已確定", "最終報價已", "價格已確認"),
}


def _expose_owner_command_behavior(composed: ComposedReply) -> bool:
    text = _text(composed)
    return any(token in text for token in _OWNER_COMMAND_TOKENS)


def check_must_not_claim(tag: str, composed: ComposedReply) -> bool | str:
    """True = the banned claim is ABSENT (i.e. the requirement PASSES).
    Returns NOT_DETERMINISTIC when no checker is registered."""
    parsed = parse_tag(tag)
    if parsed.name == "expose_owner_command_behavior":
        return not _expose_owner_command_behavior(composed)
    phrases = _BANNED_PHRASES.get(parsed.name)
    if phrases is None:
        return NOT_DETERMINISTIC
    text = _text(composed)
    return not any(phrase in text for phrase in phrases)
