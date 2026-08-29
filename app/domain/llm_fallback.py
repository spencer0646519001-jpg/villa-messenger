"""
LLM fallback for inquiry parsing.

Moat: the LLM is only allowed to help with fuzzy semantic parsing and booking
intent judgment. It never touches pricing, gates, FAQ, state machines, or any
customer-visible reply text. LLM output may only become structured slots or
single-turn clarification signals. Bad JSON, timeout, provider exceptions, or
ambiguous output all fall back to the rule-parser result unchanged.
"""

from __future__ import annotations

import os
import re
from datetime import date

from app.domain.inquiry_completeness import compute_missing_fields
from app.domain.date_parser import parse_stay_dates
from app.domain.faq_matcher import match_all_faq_topics
from app.domain.guest_count_parser import parse_guest_counts
from app.domain.llm_provider import LLMFallbackExhaustedError, LLMOutput, LLMProvider
from app.domain.parser_models import (
    DateParseResult,
    GuestCountParseResult,
    InquiryIntentResult,
    InquiryParseResult,
    PetParseResult,
)
from app.domain.text_normalizer import normalize_for_parsing

TYPE_1_DATE_TRANSLATION = "type_1_date_translation"
TYPE_2_INTENT_JUDGMENT = "type_2_intent_judgment"
TYPE_3_FAQ_BOOKING_COLLISION = "type_3_faq_booking_collision"
TYPE_4_STATE_CONTINUATION_JUDGMENT = "type_4_state_continuation_judgment"

_QUOTE_RELEVANT_INTENTS = {"price", "availability", "booking_question"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_SIGNAL_PATTERNS = (
    re.compile(r"\d{1,2}\s*/\s*\d{1,2}\s*[-~]\s*\d{1,2}"),
    re.compile(r"(?:入住|退房)\s*\d{1,2}(?!\s*(?:/|月))|\d{1,2}\s*(?:入住|退房)"),
    re.compile(r"\d{1,2}\s*(?:晚|夜|天|日遊|日游)"),
    re.compile(r"(?:下個?月|這個?月|下週|下周|本週|這週|週末|周末|連假|月底|月初|月中)"),
)


def llm_fallback_parse(
    inquiry: InquiryParseResult,
    raw_text: str,
    *,
    reference_year: int,
    is_quote_relevant: bool,
    tenant_id: int,
    provider: LLMProvider | None,
    enabled: bool | None = None,
) -> InquiryParseResult:
    if not _llm_enabled(enabled) or provider is None:
        return inquiry

    trigger = _select_trigger(inquiry, raw_text, is_quote_relevant)
    if trigger is None:
        return inquiry

    try:
        llm_out = provider.parse(
            raw_text=raw_text,
            reference_year=reference_year,
            trigger=trigger,
            tenant_id=tenant_id,
        )
    except LLMFallbackExhaustedError:
        return inquiry
    if llm_out is None:
        return inquiry
    return _merge_llm_into_inquiry(inquiry, llm_out, trigger)


def _llm_enabled(enabled: bool | None) -> bool:
    if enabled is not None:
        return enabled
    value = os.environ.get("LLM_ENABLED")
    return value is None or value.strip().lower() not in {"0", "false", "no", "off"}


def _select_trigger(
    inquiry: InquiryParseResult,
    raw_text: str,
    is_quote_relevant: bool,
) -> str | None:
    if _has_faq_booking_collision(raw_text):
        return TYPE_3_FAQ_BOOKING_COLLISION
    dates_complete = inquiry.dates.checkin_date is not None and inquiry.dates.checkout_date is not None
    if not dates_complete and _date_signal_present(raw_text):
        return TYPE_1_DATE_TRANSLATION
    if dates_complete and not is_quote_relevant:
        return TYPE_2_INTENT_JUDGMENT
    return None


def _date_signal_present(raw_text: str) -> bool:
    text = normalize_for_parsing(raw_text)
    return any(pattern.search(text) for pattern in _DATE_SIGNAL_PATTERNS)


def _has_faq_booking_collision(raw_text: str) -> bool:
    text = normalize_for_parsing(raw_text)
    dates = parse_stay_dates(text)
    has_date = dates.checkin_date is not None or dates.checkout_date is not None
    faq_matches = [
        match
        for match in match_all_faq_topics(text)
        if not (match.topic == "checkout" and has_date)
    ]
    if not faq_matches:
        return False
    has_guests = parse_guest_counts(text).guest_count is not None
    return has_date or has_guests


def _merge_llm_into_inquiry(
    inquiry: InquiryParseResult,
    llm_out: LLMOutput,
    trigger: str,
) -> InquiryParseResult:
    if trigger == TYPE_3_FAQ_BOOKING_COLLISION:
        return _merge_collision_judgment(inquiry, llm_out)
    if llm_out.needs_clarification:
        clarified = _maybe_upgrade_intent(inquiry, llm_out)
        clarified = clarified.model_copy(
            update={
                "needs_clarification": True,
                "clarification_reason": llm_out.clarification_reason,
            }
        )
        return _recompute_flags(clarified)

    if trigger == TYPE_2_INTENT_JUDGMENT and llm_out.is_booking_intent is False:
        # Deliberately does NOT touch inquiry_type/is_inquiry (still
        # "unknown"/False, per test_type_2_non_booking_does_not_upgrade_or_
        # mutate_slots) -- only records that this was a CONFIRMED rejection,
        # not an unjudged case, so callers like ConversationStateService can
        # tell the two apart. Codex review of commit 0027fec (P2).
        return inquiry.model_copy(update={"llm_rejected_booking_intent": True})

    merged = _merge_slots(inquiry, llm_out)
    merged = _maybe_upgrade_intent(merged, llm_out)
    return _recompute_flags(merged)


def _merge_collision_judgment(
    inquiry: InquiryParseResult, llm_out: LLMOutput
) -> InquiryParseResult:
    judgment = _collision_booking_judgment(llm_out)
    merged = inquiry.model_copy(
        update={"llm_detected_intents": list(llm_out.intents)}
    )
    if judgment is None:
        return merged
    if judgment:
        mapped = _map_llm_intent(llm_out.intent) or "availability"
        merged = merged.model_copy(
            update={
                "intent": InquiryIntentResult(
                    is_inquiry=True,
                    inquiry_type=mapped,
                )
            }
        )
    else:
        merged = merged.model_copy(
            update={
                "intent": InquiryIntentResult(
                    is_inquiry=True,
                    inquiry_type="faq",
                )
            }
        )
    return _recompute_flags(merged)


def _collision_booking_judgment(llm_out: LLMOutput) -> bool | None:
    if llm_out.is_booking_intent is not None:
        return llm_out.is_booking_intent
    if llm_out.intent in ("price", "availability", "booking"):
        return True
    if llm_out.intent == "faq":
        return False
    return None


def _merge_slots(inquiry: InquiryParseResult, llm_out: LLMOutput) -> InquiryParseResult:
    return inquiry.model_copy(
        update={
            "dates": _merge_dates(inquiry.dates, llm_out),
            "guests": _merge_guests(inquiry.guests, llm_out),
            "pets": _merge_pets(inquiry.pets, llm_out),
        }
    )


def _merge_dates(dates: DateParseResult, llm_out: LLMOutput) -> DateParseResult:
    checkin = _valid_iso_date_or_none(llm_out.checkin_date) or dates.checkin_date
    checkout = _valid_iso_date_or_none(llm_out.checkout_date) or dates.checkout_date
    nights = _nights_between(checkin, checkout)
    return dates.model_copy(
        update={
            "checkin_date": checkin,
            "checkout_date": checkout,
            "nights": nights,
            "confidence": "high" if nights is not None else dates.confidence,
            "missing_fields": _date_missing_fields(checkin, checkout),
        }
    )


def _merge_guests(guests: GuestCountParseResult, llm_out: LLMOutput) -> GuestCountParseResult:
    adult = _valid_count_or_none(llm_out.adult_count, guests.adult_count)
    child = _valid_count_or_none(llm_out.child_count, guests.child_count)
    infant = _valid_count_or_none(llm_out.infant_count, guests.infant_count)
    guest_count = _guest_count(adult, child, guests.guest_count, llm_out)
    return guests.model_copy(
        update={
            "adult_count": adult,
            "child_count": child,
            "infant_count": infant,
            "guest_count": guest_count,
            "confidence": "high" if guest_count is not None or infant is not None else guests.confidence,
            "needs_child_confirmation": child is not None,
            "needs_infant_confirmation": infant is not None,
        }
    )


def _merge_pets(pets: PetParseResult, llm_out: LLMOutput) -> PetParseResult:
    pet_count = _valid_count_or_none(llm_out.pet_count, pets.pet_count)
    has_pet = llm_out.has_pet if isinstance(llm_out.has_pet, bool) else pets.has_pet
    if llm_out.pet_count is not None:
        has_pet = pet_count is not None and pet_count > 0
    return pets.model_copy(
        update={
            "has_pet": has_pet,
            "pet_count": pet_count,
            "needs_pet_count_confirmation": has_pet and pet_count is None,
        }
    )


def _valid_iso_date_or_none(value: str | None) -> str | None:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _valid_count_or_none(value: int | None, fallback: int | None) -> int | None:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return fallback
    return value


def _guest_count(
    adult: int | None,
    child: int | None,
    existing: int | None,
    llm_out: LLMOutput,
) -> int | None:
    if llm_out.adult_count is not None or llm_out.child_count is not None:
        return (adult or 0) + (child or 0)
    return existing


def _nights_between(checkin: str | None, checkout: str | None) -> int | None:
    if checkin is None or checkout is None:
        return None
    delta_days = (date.fromisoformat(checkout) - date.fromisoformat(checkin)).days
    return delta_days if delta_days > 0 else None


def _date_missing_fields(checkin: str | None, checkout: str | None) -> list[str]:
    missing = []
    if checkin is None:
        missing.append("checkin_date")
    if checkout is None:
        missing.append("checkout_date")
    return missing


def _maybe_upgrade_intent(
    inquiry: InquiryParseResult,
    llm_out: LLMOutput,
) -> InquiryParseResult:
    mapped_intent = _map_llm_intent(llm_out.intent)
    if mapped_intent is None and llm_out.is_booking_intent is True:
        mapped_intent = "booking_question"
    if mapped_intent not in _QUOTE_RELEVANT_INTENTS:
        return inquiry
    return inquiry.model_copy(
        update={
            "intent": InquiryIntentResult(
                is_inquiry=True,
                inquiry_type=mapped_intent,
            )
        }
    )


def _map_llm_intent(intent: str | None) -> str | None:
    if intent == "booking":
        return "booking_question"
    if intent in _QUOTE_RELEVANT_INTENTS:
        return intent
    return None


def _recompute_flags(inquiry: InquiryParseResult) -> InquiryParseResult:
    quote_relevant = inquiry.intent.is_inquiry and inquiry.intent.inquiry_type in _QUOTE_RELEVANT_INTENTS
    missing_fields = _missing_fields(inquiry) if quote_relevant else []
    can_quote = _can_preliminarily_quote(inquiry, quote_relevant)
    return inquiry.model_copy(
        update={
            "missing_fields": missing_fields,
            "can_preliminarily_quote": can_quote,
        }
    )


def _missing_fields(inquiry: InquiryParseResult) -> list[str]:
    return compute_missing_fields(
        checkin_date=inquiry.dates.checkin_date,
        checkout_date=inquiry.dates.checkout_date,
        guest_count=inquiry.guests.guest_count,
        has_pet=inquiry.pets.has_pet,
        pet_count=inquiry.pets.pet_count,
    )


def _can_preliminarily_quote(inquiry: InquiryParseResult, quote_relevant: bool) -> bool:
    return (
        quote_relevant
        and inquiry.dates.checkin_date is not None
        and inquiry.dates.checkout_date is not None
        and inquiry.dates.nights is not None
        and inquiry.dates.nights > 0
        and inquiry.guests.guest_count is not None
        and (not inquiry.pets.has_pet or inquiry.pets.pet_count is not None)
    )


def judge_state_continuation(
    *,
    state: dict,
    raw_text: str,
    reference_year: int,
    tenant_id: int,
    provider: LLMProvider | None,
    enabled: bool | None = None,
) -> bool | None:
    """Single-purpose judgment for an open (in_progress) conversation_states row:
    given what's already known and still missing, is this new message still
    trying to continue that booking conversation, or has the customer moved on
    to something else? Returns None (never a guess) when the LLM is disabled,
    unavailable, or fails -- callers must treat None as "keep today's
    rule-based behavior unchanged", never as a false negative.

    Does NOT extract slots and does not touch reply text, pricing, or the
    state machine itself -- the caller alone decides what to do with the
    True/False/None verdict."""
    if not _llm_enabled(enabled) or provider is None:
        return None
    try:
        llm_out = provider.parse(
            raw_text=_state_continuation_context_text(state, raw_text),
            reference_year=reference_year,
            trigger=TYPE_4_STATE_CONTINUATION_JUDGMENT,
            tenant_id=tenant_id,
        )
    except LLMFallbackExhaustedError:
        return None
    if llm_out is None:
        return None
    return llm_out.is_booking_intent


def _state_continuation_context_text(state: dict, raw_text: str) -> str:
    guest_count = (state.get("adult_count") or 0) + (state.get("child_count") or 0)
    known_parts = []
    if state.get("checkin_date"):
        known_parts.append(f"入住 {state['checkin_date']}")
    if state.get("checkout_date"):
        known_parts.append(f"退房 {state['checkout_date']}")
    if guest_count:
        known_parts.append(f"人數 {guest_count}")
    if state.get("has_pet"):
        known_parts.append("有寵物")
    missing_parts = []
    if not state.get("checkin_date") or not state.get("checkout_date"):
        missing_parts.append("入住/退房日期")
    if not guest_count:
        missing_parts.append("人數")
    if state.get("room_count") is None:
        missing_parts.append("房數")
    known_summary = "、".join(known_parts) if known_parts else "尚無"
    missing_summary = "、".join(missing_parts) if missing_parts else "無"
    return (
        f"[訂房對話已知資訊:{known_summary};還缺:{missing_summary}]\n"
        f"客人最新一句話:{raw_text}"
    )
