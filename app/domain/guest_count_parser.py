import re

from app.domain.number_parser import parse_chinese_or_arabic_number
from app.domain.parser_models import GuestCountParseResult


_NUMBER_PATTERN = r"\d+|十[一二兩三四五六]?|[一二兩三四五六七八九十]"

_NUMBER_BEFORE_ADULT = re.compile(rf"(?P<count>{_NUMBER_PATTERN})\s*(?:大人|成人|大)")
_NUMBER_BEFORE_CHILD = re.compile(rf"(?P<count>{_NUMBER_PATTERN})\s*(?:小孩|小朋友|兒童|小)")
_NUMBER_BEFORE_INFANT = re.compile(rf"(?P<count>{_NUMBER_PATTERN})\s*(?:嬰兒|嬰|幼兒|寶寶)")

_ADULT_BEFORE_NUMBER = re.compile(rf"(?:大人|成人)\s*(?P<count>{_NUMBER_PATTERN})\s*(?:位|人)?")
_CHILD_BEFORE_NUMBER = re.compile(rf"(?:小孩|小朋友|兒童)\s*(?P<count>{_NUMBER_PATTERN})\s*(?:位|人)?")
_INFANT_BEFORE_NUMBER = re.compile(rf"(?:嬰兒|嬰|幼兒|寶寶)\s*(?P<count>{_NUMBER_PATTERN})\s*(?:位|人)?")

_TOTAL_GUESTS = re.compile(rf"(?:總共|一共|共)?\s*(?P<count>{_NUMBER_PATTERN})\s*(?:個)?\s*(?:人|位)")


def parse_guest_counts(text: str) -> GuestCountParseResult:
    adult_count = _first_count(text, (_NUMBER_BEFORE_ADULT, _ADULT_BEFORE_NUMBER))
    child_count = _first_count(text, (_NUMBER_BEFORE_CHILD, _CHILD_BEFORE_NUMBER))
    infant_count = _first_count(text, (_NUMBER_BEFORE_INFANT, _INFANT_BEFORE_NUMBER))

    guest_count = None
    if adult_count is not None or child_count is not None:
        guest_count = (adult_count or 0) + (child_count or 0)
    else:
        guest_count = _first_count(text, (_TOTAL_GUESTS,))
        # conversation_states has no guest_count column; total-only counts are
        # treated as adults so multi-turn pricing can retain the value.
        adult_count = guest_count

    has_any_count = any(
        count is not None for count in (adult_count, child_count, infant_count, guest_count)
    )

    return GuestCountParseResult(
        adult_count=adult_count,
        child_count=child_count,
        infant_count=infant_count,
        guest_count=guest_count,
        confidence="high" if has_any_count else "low",
        needs_child_confirmation=child_count is not None,
        needs_infant_confirmation=infant_count is not None,
    )


def _first_count(text: str, patterns: tuple[re.Pattern[str], ...]) -> int | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match is None:
            continue
        parsed = parse_chinese_or_arabic_number(match.group("count"))
        if parsed is not None:
            return parsed
    return None
