"""Build temporary date ranges used only to probe calendar availability."""

import re
from datetime import date, timedelta

from app.domain.parser_models import InquiryParseResult
from app.domain.text_normalizer import normalize_for_parsing


_QUOTE_RELEVANT_INTENTS = {"price", "availability", "booking_question"}
_COUNT = r"(?:\d+|[一二兩三四五六七八九十百]+)"
_EXPLICIT_STAY_PERIOD_PATTERNS = (
    re.compile(rf"{_COUNT}\s*(?:晚|夜)"),
    re.compile(rf"{_COUNT}\s*天(?:\s*{_COUNT}\s*夜)?"),
    re.compile(r"(?:住到|到|至|~|～|—|-)\s*(?:\d{1,2}\s*(?:/|月)\s*)?\d{1,2}"),
)


def with_single_night_availability_probe(
    inquiry: InquiryParseResult,
    raw_text: str,
) -> InquiryParseResult:
    """Attach an inferred checkout used only for a one-night availability probe.

    The real checkout slot, missing_fields, and preliminary-pricing flags are
    deliberately untouched: an assumption is safe for an early calendar probe,
    but must never become a customer-supplied stay date or enter pricing.
    """
    if not _can_infer_probe(inquiry, raw_text):
        return inquiry
    checkin = date.fromisoformat(inquiry.dates.checkin_date)
    return inquiry.model_copy(
        update={
            "availability_probe_checkout": (checkin + timedelta(days=1)).isoformat(),
            "availability_probe_checkout_was_inferred": True,
        }
    )


def _can_infer_probe(inquiry: InquiryParseResult, raw_text: str) -> bool:
    if not (
        inquiry.intent.is_inquiry
        and inquiry.intent.inquiry_type in _QUOTE_RELEVANT_INTENTS
    ):
        return False
    if inquiry.dates.checkin_date is None or inquiry.dates.checkout_date is not None:
        return False
    return not _has_explicit_stay_period(raw_text)


def _has_explicit_stay_period(raw_text: str) -> bool:
    text = normalize_for_parsing(raw_text)
    return any(pattern.search(text) for pattern in _EXPLICIT_STAY_PERIOD_PATTERNS)
