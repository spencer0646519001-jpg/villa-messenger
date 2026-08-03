import re
from datetime import date, datetime

from app.domain.parser_models import DateParseResult


_DATE_PATTERN = re.compile(
    r"(?<![\d/])(?P<month>0?[1-9]|1[0-2])\s*(?:/|月)\s*"
    # [ \t]*, not \s*, before the optional 日 suffix: a bare \s* would swallow
    # a trailing newline into the match itself, hiding it from
    # _has_close_label_after's own newline check (it only sees text AFTER
    # match.end()).
    r"(?P<day>0?[1-9]|[12]\d|3[01])[ \t]*(?:日)?(?!\d)"
)
_CHECKIN_LABELS = ("入住",)
_CHECKOUT_LABELS = ("退房",)


def parse_stay_dates(text: str, reference_year: int | None = None) -> DateParseResult:
    year = reference_year if reference_year is not None else datetime.now().year
    date_matches = list(_valid_date_matches(text, year))

    checkin = None
    checkout = None
    unlabeled_dates: list[date] = []

    for parsed_date, start, end in date_matches:
        label = _classify_date_label(text, start, end)
        if label == "checkin" and checkin is None:
            checkin = parsed_date
        elif label == "checkout" and checkout is None:
            checkout = parsed_date
        elif label is None:
            unlabeled_dates.append(parsed_date)

    if checkin is None and checkout is None and len(unlabeled_dates) == 2:
        checkin, checkout = unlabeled_dates
    elif checkin is None and checkout is None and len(date_matches) == 1:
        checkin = date_matches[0][0]

    nights = None
    confidence = "low"
    if checkin is not None and checkout is not None:
        delta_days = (checkout - checkin).days
        if delta_days > 0:
            nights = delta_days
            confidence = "high"

    missing_fields = []
    if checkin is None:
        missing_fields.append("checkin_date")
    if checkout is None:
        missing_fields.append("checkout_date")

    return DateParseResult(
        checkin_date=checkin.isoformat() if checkin is not None else None,
        checkout_date=checkout.isoformat() if checkout is not None else None,
        nights=nights,
        confidence=confidence,
        missing_fields=missing_fields,
    )


def _valid_date_matches(text: str, year: int) -> list[tuple[date, int, int]]:
    matches = []
    for match in _DATE_PATTERN.finditer(text):
        month = int(match.group("month"))
        day = int(match.group("day"))
        try:
            parsed_date = date(year, month, day)
        except ValueError:
            continue
        matches.append((parsed_date, match.start(), match.end()))
    return matches


def _classify_date_label(text: str, start: int, end: int) -> str | None:
    if _has_close_label_before(text, start, _CHECKIN_LABELS):
        return "checkin"
    if _has_close_label_before(text, start, _CHECKOUT_LABELS):
        return "checkout"
    if _has_close_label_after(text, end, _CHECKIN_LABELS):
        return "checkin"
    if _has_close_label_after(text, end, _CHECKOUT_LABELS):
        return "checkout"
    return None


def _has_close_label_before(text: str, start: int, labels: tuple[str, ...]) -> bool:
    # Don't cross a newline -- a label on a PRECEDING form field's line (e.g.
    # "聯絡電話:0912345678\n入住日期:8/10-8/12") must not attach to this date.
    line_start = text.rfind("\n", 0, start) + 1
    search_start = max(0, start - 8, line_start)
    for label in labels:
        label_start = text.rfind(label, search_start, start)
        if label_start == -1:
            continue

        label_end = label_start + len(label)
        between_label_and_date = text[label_end:start]
        if re.sub(r"[\s，,、;；:：-]+", "", between_label_and_date):
            continue

        previous_char = _previous_significant_char(text, label_start)
        is_checkin_label = any(label in _CHECKIN_LABELS for label in labels)
        if is_checkin_label and (previous_char.isdigit() or previous_char in {"/", "日"}):
            continue

        return True

    return False


def _has_close_label_after(text: str, end: int, labels: tuple[str, ...]) -> bool:
    # Don't cross a newline -- e.g. "入住日期:8/10-8/12\n入住人數:8人" must not
    # let the NEXT field's "入住人數" label attach to this date as "checkin".
    newline_pos = text.find("\n", end, end + 6)
    suffix_end = newline_pos if newline_pos != -1 else end + 6
    suffix = text[end:suffix_end]
    compact_suffix = re.sub(r"\s+", "", suffix)
    return any(compact_suffix.startswith(label) for label in labels)


def _previous_significant_char(text: str, index: int) -> str:
    cursor = index - 1
    while cursor >= 0 and re.fullmatch(r"[\s，,、;；:：-]", text[cursor]):
        cursor -= 1
    return text[cursor] if cursor >= 0 else ""
