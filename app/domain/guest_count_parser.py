import re

from app.domain.number_parser import parse_chinese_or_arabic_number
from app.domain.parser_models import GuestCountParseResult

_NUMBER_PATTERN = r"\d+|十[一二兩三四五六]?|[一二兩三四五六七八九十]"

_ADULT_LABELS = ("大人", "成人")
_CHILD_LABELS = ("小孩", "小朋友", "兒童")
_INFANT_LABELS = ("嬰兒", "嬰", "幼兒", "寶寶")

_NUMBER_BEFORE_ADULT = re.compile(rf"(?P<count>{_NUMBER_PATTERN})\s*位?\s*(?:大人|成人|大)")
_NUMBER_BEFORE_CHILD = re.compile(rf"(?P<count>{_NUMBER_PATTERN})\s*位?\s*(?:小孩|小朋友|兒童|小)")
_NUMBER_BEFORE_INFANT = re.compile(rf"(?P<count>{_NUMBER_PATTERN})\s*位?\s*(?:嬰兒|嬰|幼兒|寶寶)")

_ADULT_BEFORE_NUMBER = re.compile(rf"(?:大人|成人)\s*(?P<count>{_NUMBER_PATTERN})\s*(?:位|人)?")
_CHILD_BEFORE_NUMBER = re.compile(rf"(?:小孩|小朋友|兒童)\s*(?P<count>{_NUMBER_PATTERN})\s*(?:位|人)?")
_INFANT_BEFORE_NUMBER = re.compile(rf"(?:嬰兒|嬰|幼兒|寶寶)\s*(?P<count>{_NUMBER_PATTERN})\s*(?:位|人)?")

_TOTAL_GUESTS = re.compile(rf"(?:總共|一共|共)?\s*(?P<count>{_NUMBER_PATTERN})\s*(?:個)?\s*(?:人|位)")

# "NUMBER位?" ending right where a label starts (allowing a whitespace gap,
# e.g. "10 大人") -- i.e. that label already got its own count in
# label-before-number order, so it will not also claim the number that
# follows it (see _has_label_theft below).
_TRAILING_NUMBER = re.compile(rf"(?:{_NUMBER_PATTERN})位?\s*$")


def parse_guest_counts(text: str) -> GuestCountParseResult:
    adult_count = _extract_count(
        text, _NUMBER_BEFORE_ADULT, _ADULT_BEFORE_NUMBER, _CHILD_LABELS + _INFANT_LABELS
    )
    child_count = _extract_count(
        text, _NUMBER_BEFORE_CHILD, _CHILD_BEFORE_NUMBER, _ADULT_LABELS + _INFANT_LABELS
    )
    infant_count = _extract_count(
        text, _NUMBER_BEFORE_INFANT, _INFANT_BEFORE_NUMBER, _ADULT_LABELS + _CHILD_LABELS
    )

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


def _extract_count(
    text: str,
    number_before_label: re.Pattern[str],
    label_before_number: re.Pattern[str],
    other_category_labels: tuple[str, ...],
) -> int | None:
    """Try 'N位label' first (rejecting matches that actually belong to a
    DIFFERENT category's own label-before-number count, e.g. the "2" in
    "大人2位小孩1位" belongs to 大人, not to a phantom "2位小孩"), then fall
    back to the existing 'label N位' order."""
    match = _first_unstolen_match(text, number_before_label, other_category_labels)
    if match is not None:
        parsed = parse_chinese_or_arabic_number(match.group("count"))
        if parsed is not None:
            return parsed
    return _first_count(text, (label_before_number,))


def _first_unstolen_match(
    text: str, pattern: re.Pattern[str], other_category_labels: tuple[str, ...]
) -> re.Match[str] | None:
    for match in pattern.finditer(text):
        if not _has_label_theft(text, match.start(), other_category_labels):
            return match
    return None


def _has_label_theft(text: str, number_start: int, other_category_labels: tuple[str, ...]) -> bool:
    prefix = text[:number_start]
    for label in other_category_labels:
        # Allow whitespace between the OTHER category's label and the number
        # it already claimed (e.g. "大人 2位小孩 1位" -- 大人's own "2位" is
        # separated from 小孩 by a space, not adjacent to it), otherwise a
        # bare endswith(label) misses the gap and lets 小孩 steal the "2"
        # that already belongs to 大人.
        match = re.search(rf"(?:{re.escape(label)})\s*$", prefix)
        if match is not None:
            label_start = match.start()
            return _TRAILING_NUMBER.search(text[:label_start]) is None
    return False


def _first_count(text: str, patterns: tuple[re.Pattern[str], ...]) -> int | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match is None:
            continue
        parsed = parse_chinese_or_arabic_number(match.group("count"))
        if parsed is not None:
            return parsed
    return None
