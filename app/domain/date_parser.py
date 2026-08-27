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
# "7/17-18" shorthand: a bare day sharing the month of the date just matched
# (as opposed to "7/17-8/12", where the second side is already a full M/D
# date and matches _DATE_PATTERN on its own). The negative lookaheads mirror
# _DATE_PATTERN's so a full M/D date on the far side is never double-counted,
# plus a guard against a following colon/點 so a hyphenated clock time (e.g.
# "7/17-18:00") is never read as a second date -- flagged by Codex review of
# commit eec20a8 (P1): without it, "8/10-8/12-14:00" produced three date
# matches and silently discarded BOTH real stay dates (the ==2 pairing
# fallback below requires exactly two unlabeled dates).
_RANGE_SEPARATOR_DAY_PATTERN = re.compile(
    r"[-~～至到]\s*(?P<day>0?[1-9]|[12]\d|3[01])[ \t]*(?:日)?(?!\s*(?:/|月|[:：]|點))(?!\d)"
)


def parse_stay_dates(text: str, reference_year: int | None = None) -> DateParseResult:
    year = reference_year if reference_year is not None else datetime.now().year
    date_matches, shorthand_pairs = _valid_date_matches(text, year)

    checkin = None
    checkout = None
    unlabeled: dict[int, date] = {}
    labels: dict[int, str | None] = {}

    for idx, (parsed_date, start, end) in enumerate(date_matches):
        label = _classify_date_label(text, start, end)
        labels[idx] = label
        if label == "checkin" and checkin is None:
            checkin = parsed_date
        elif label == "checkout" and checkout is None:
            checkout = parsed_date
        elif label is None:
            unlabeled[idx] = parsed_date

    checkin, checkout = _resolve_shorthand_pairs(
        shorthand_pairs, date_matches, labels, unlabeled, checkin, checkout
    )
    unlabeled_dates = list(unlabeled.values())

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


def _valid_date_matches(
    text: str, year: int
) -> tuple[list[tuple[date, int, int]], list[tuple[int, int]]]:
    matches: list[tuple[date, int, int]] = []
    shorthand_pairs: list[tuple[int, int]] = []
    for match in _DATE_PATTERN.finditer(text):
        month = int(match.group("month"))
        day = int(match.group("day"))
        try:
            parsed_date = date(year, month, day)
        except ValueError:
            continue
        primary_idx = len(matches)
        matches.append((parsed_date, match.start(), match.end()))
        suffix_matches = _range_suffix_match(text, match.end(), year, month)
        if suffix_matches:
            matches.extend(suffix_matches)
            shorthand_pairs.append((primary_idx, primary_idx + 1))
    return matches, shorthand_pairs


def _resolve_shorthand_pairs(
    shorthand_pairs: list[tuple[int, int]],
    date_matches: list[tuple[date, int, int]],
    labels: dict[int, str | None],
    unlabeled: dict[int, date],
    checkin: date | None,
    checkout: date | None,
) -> tuple[date | None, date | None]:
    # A shorthand pair ("7/17-18") is strong-enough evidence on its own that
    # if EITHER side already got a label (e.g. "7/17-18退房" labels only the
    # suffix as checkout), the other side fills the opposite, still-empty
    # role -- rather than sitting stranded in unlabeled_dates, unused, because
    # the plain "exactly two unlabeled dates" fallback below never sees it.
    # Flagged by Codex review of commit eec20a8 (P2).
    for lo_idx, hi_idx in shorthand_pairs:
        if labels[lo_idx] == "checkin" and labels[hi_idx] is None and checkout is None:
            checkout = date_matches[hi_idx][0]
            unlabeled.pop(hi_idx, None)
        elif labels[hi_idx] == "checkout" and labels[lo_idx] is None and checkin is None:
            checkin = date_matches[lo_idx][0]
            unlabeled.pop(lo_idx, None)
    return checkin, checkout


def _range_suffix_match(
    text: str, after: int, year: int, month: int
) -> list[tuple[date, int, int]]:
    suffix = _RANGE_SEPARATOR_DAY_PATTERN.match(text, after)
    if suffix is None:
        return []
    try:
        parsed_date = date(year, month, int(suffix.group("day")))
    except ValueError:
        return []
    return [(parsed_date, suffix.start("day"), suffix.end("day"))]


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
