import re
from datetime import date, datetime

from app.domain.parser_models import DateParseResult


_DATE_PATTERN = re.compile(
    r"(?<![\d/])(?P<month>0?[1-9]|1[0-2])\s*(?:/|月)\s*"
    # [ \t]*, not \s*, before the optional 日 suffix: a bare \s* would swallow
    # a trailing newline into the match itself, hiding it from
    # _has_close_label_after's own newline check (it only sees text AFTER
    # match.end()).
    # A third slash-separated component means this is not a stay date:
    # "開 4/4/2" is a room configuration (a 4-person room, a 4-person room
    # and a 2-person room), and reading its first two parts as 4 April sent a
    # real customer an availability answer for a date she never mentioned.
    # This module has no year support, so a real stay date can never carry a
    # third "/" component -- rejecting it here costs nothing. Full-width ／ is
    # listed too: parse_stay_dates is called on raw text in a few places
    # (inquiry_intent, form_reply_detector) that skip normalize_for_parsing.
    r"(?P<day>0?[1-9]|[12]\d|3[01])[ \t]*(?:日)?(?!\d)(?![ \t]*[/／])"
)
_CHECKIN_LABELS = ("入住",)
_CHECKOUT_LABELS = ("退房",)
# "7/17-18" shorthand: a bare day sharing the month of the date just matched
# (as opposed to "7/17-8/12", where the second side is already a full M/D
# date and matches _DATE_PATTERN on its own). The negative lookaheads mirror
# _DATE_PATTERN's so a full M/D date on the far side is never double-counted,
# plus a guard against a following clock time so a hyphenated time (e.g.
# "7/17-18:00" or "入住7/17-18時") is never read as a second date -- flagged
# by Codex review of commit eec20a8 (P1): without it, "8/10-8/12-14:00"
# produced three date matches and silently discarded BOTH real stay dates
# (the ==2 pairing fallback below requires exactly two unlabeled dates).
# The colon check requires an unspaced two-digit minute (":00", not ": 2")
# so a field separator like "7/17-18: 2人" isn't mistaken for a clock time
# and doesn't lose the range -- flagged by Codex review of commit ac8f084
# (P1 缺 時, P2 冒號規則過寬).
_RANGE_SEPARATOR_DAY_PATTERN = re.compile(
    r"[-~～至到]\s*(?P<day>0?[1-9]|[12]\d|3[01])[ \t]*(?:日)?"
    r"(?!\s*(?:/|月|點|時))(?![:：]\d{2}(?!\d))(?!\d)"
)


# Year words the rule layer resolves itself. Before this existed, "明年 2/6-8"
# parsed as THIS year's 2/6 -- a date seven months in the past -- and the only
# reason a real customer's 春節 enquiry came out as 2027 was that her message
# also said "2台車", which tripped the FAQ-collision LLM trigger and the LLM
# happened to fix the year on its way past. Year arithmetic is deterministic;
# it does not belong on that lucky path.
_YEAR_WORD_OFFSETS = {"今年": 0, "明年": 1, "隔年": 1, "後年": 2}
_YEAR_WORD_PATTERN = re.compile("|".join(_YEAR_WORD_OFFSETS))


def _year_word_positions(text: str) -> list[tuple[int, int]]:
    return [
        (match.start(), _YEAR_WORD_OFFSETS[match.group()])
        for match in _YEAR_WORD_PATTERN.finditer(text)
    ]


def _basis_for_match(
    start: int,
    base_year: int,
    reference_date: date | None,
    positions: list[tuple[int, int]],
) -> tuple[int, date | None]:
    """Year basis for the one date beginning at `start`.

    Each date takes the nearest year word BEFORE it, rather than the message
    taking a single one. "今年12/31入住，明年1/2退房" is an ordinary way to write
    a new-year stay and means two different years; so does the explicit
    "明年12/31入住，後年1/2退房". Both went a year wrong under a message-wide
    rule -- Codex review of commits 8c9aeb3 and fea1e5e (P1 twice, which is
    what moved this from "documented limitation" to "fix the class").

    A date with no year word before it falls back to roll-forward, and an
    explicit word switches roll-forward off for that date: the customer named
    their year and guessing past them would be wrong.
    """
    offset = None
    for position, value in positions:
        if position >= start:
            break
        offset = value
    if offset is None:
        return base_year, reference_date
    return base_year + offset, None


def _build_date(year: int, month: int, day: int, not_before: date | None) -> date | None:
    """Build (month, day), rolling to the next year when it has already passed.

    Only year and year + 1 are tried: that covers every real month/day, and
    the one shape it does not -- 2/29 in a run of non-leap years -- keeps the
    pre-existing "silently drop an impossible date" behaviour rather than
    quietly jumping a customer two years forward.
    """
    if not_before is None:
        try:
            return date(year, month, day)
        except ValueError:
            return None
    for candidate_year in (year, year + 1):
        try:
            built = date(candidate_year, month, day)
        except ValueError:
            continue
        if built >= not_before:
            return built
    return None


def parse_stay_dates(
    text: str,
    reference_year: int | None = None,
    *,
    reference_date: date | None = None,
) -> DateParseResult:
    if reference_date is None:
        # Legacy path: every pre-existing caller and test. No roll-forward, no
        # year words, one base year for the whole message -- exactly as before.
        base_year = reference_year if reference_year is not None else datetime.now().year
        positions: list[tuple[int, int]] = []
    else:
        base_year = reference_date.year
        positions = _year_word_positions(text)
    date_matches, range_pairs = _valid_date_matches(
        text, base_year, reference_date, positions
    )

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

    checkin, checkout = _resolve_range_pairs(
        range_pairs, date_matches, labels, unlabeled, checkin, checkout
    )
    unlabeled_dates = list(unlabeled.values())

    if checkin is None and checkout is None and len(unlabeled_dates) == 2:
        checkin, checkout = unlabeled_dates
    elif checkin is None and checkout is None and len(date_matches) == 1:
        checkin = date_matches[0][0]

    if reference_date is not None:
        # A message carrying any year word has had its years stated, so
        # reconciliation may only carry a departure over new year, never
        # re-pick a year the customer gave.
        checkin, checkout = _reconcile_stay_years(
            checkin, checkout, None if positions else reference_date
        )

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


# Deliberately NOT here: a semantic "開 means rooms, not a date" rule. Two
# rounds of Codex review killed it, and the second failure was worse than the
# bug it was meant to catch. Suppressing a single match lets the OTHER end of
# a range survive alone -- "民宿開 4/4-4/6 的房間嗎?" kept only 4/6 and would
# have quietly checked availability for the wrong dates, which beats reading
# a room list as a date on the "silently wrong" scale. And any character-level
# test for the room sense also fires on "有開 4/4 嗎?" / "民宿開 4/4-4/6 的
# 房間嗎?", where 開 means "open for business" and the date IS the question.
#
# The remaining gap is the two-room answer "開 4/4，2間" (no third slash, so
# the guard on _DATE_PATTERN above does not see it). That shape has never
# appeared in production or eval data -- it was reasoned out, not observed --
# so it stays unhandled rather than justifying a mechanism with this track
# record. If it ever does show up, the right home is the conversation-state
# layer (ConversationStateService._fill_contextual_room_count), which knows
# the system just asked "要開幾間房?" and can disambiguate from real state
# instead of guessing from neighbouring characters.
# No real stay at this homestay runs longer than a month, so a "stay" longer
# than this is proof that some year guess is wrong rather than a long booking.
_MAX_PLAUSIBLE_STAY_NIGHTS = 31


def _shifted_year(value: date, years: int) -> date | None:
    try:
        return value.replace(year=value.year + years)
    except ValueError:  # 2/29 landing on a non-leap year
        return None


def _reconcile_stay_years(
    checkin: date | None, checkout: date | None, not_before: date | None
) -> tuple[date | None, date | None]:
    """Settle the two ends of a stay onto years that make sense together.

    _build_date rolls each date on its own, which is right for a whole range
    that has passed ("2/6-2/8" asked in September) but wrong when a range
    straddles today: on 5/13, "5/12 入住 5/14 退房" would put the arrival in
    next May and the departure in this one -- a 364-night stay. The literal
    reading is one unit, so repair it as one: try the smallest year shift that
    yields a plausible stay, preferring to pull a date back over pushing one
    forward, and require the stay to still end in the future so nothing is
    dragged wholly into the past.

    "12/31入住 1/2退房" needs this too, from the other direction. It used to
    produce two same-year dates, fail pricing's checkout > checkin check, and
    send the customer a "your dates look out of order" question -- every
    new-year stay was rejected that way.

    When no shift works the dates are returned untouched, so a genuine typo
    ("5/14 入住 5/12 退房") still reaches the existing invalid-date reply
    instead of being bent into a year-long booking.
    """
    if checkin is None or checkout is None:
        return checkin, checkout

    def plausible(start: date, end: date) -> bool:
        nights = (end - start).days
        if not 0 < nights <= _MAX_PLAUSIBLE_STAY_NIGHTS:
            return False
        return not_before is None or end >= not_before

    if plausible(checkin, checkout):
        return checkin, checkout
    if not_before is None:
        # The customer named the year ("明年 12/31入住 1/2退房"). Their year is
        # not up for revision, so the only repair on offer is carrying the
        # departure over new year.
        candidates = ((checkin, _shifted_year(checkout, 1)),)
    else:
        candidates = (
            (_shifted_year(checkin, -1), checkout),
            (checkin, _shifted_year(checkout, 1)),
            (checkin, _shifted_year(checkout, -1)),
            (_shifted_year(checkin, 1), checkout),
        )
    for start, end in candidates:
        if start is not None and end is not None and plausible(start, end):
            return start, end
    # Nothing works, so the input itself is broken. Collapse to the literal
    # same-year reading rather than leaving the independently-rolled pair:
    # on 5/13, "5/14 入住 5/12 退房" rolls into 2026-05-14 -> 2027-05-12, which
    # pricing would happily accept as a 363-night booking. Same-year makes the
    # contradiction visible again and the customer gets the existing
    # "your dates look out of order" question.
    collapsed = _shifted_year(checkout, checkin.year - checkout.year)
    return checkin, collapsed if collapsed is not None else checkout


def _valid_date_matches(
    text: str,
    base_year: int,
    reference_date: date | None,
    positions: list[tuple[int, int]],
) -> tuple[list[tuple[date, int, int]], list[tuple[int, int]]]:
    matches: list[tuple[date, int, int]] = []
    for match in _DATE_PATTERN.finditer(text):
        month = int(match.group("month"))
        day = int(match.group("day"))
        year, not_before = _basis_for_match(
            match.start(), base_year, reference_date, positions
        )
        parsed_date = _build_date(year, month, day, not_before)
        if parsed_date is None:
            continue
        matches.append((parsed_date, match.start(), match.end()))
        suffix_matches = _range_suffix_match(
            text, match.end(), parsed_date.year, month, not_before
        )
        matches.extend(suffix_matches)
    return matches, _find_range_pairs(text, matches)


# Two adjacent matches with nothing but a range separator (and optional
# whitespace) between them are one "A-B" range, whether B is a bare-day
# shorthand ("7/17-18") or a full second M/D date ("8/20-8/22") -- both need
# the same label-repair logic below, so both are detected here instead of
# only the shorthand case. Leading [ \t]* (not \s*) before the separator --
# a Chinese-style date match ("8月20日") ends right after "日" with no
# trailing-space consumption of its own, so a spaced separator ("8月20日 -
# 8月22日") leaves that space in the gap being checked here; trailing \s*
# (not [ \t]*) after the separator to match _RANGE_SEPARATOR_DAY_PATTERN's
# own tolerance for a shorthand suffix wrapped onto the next line ("入住
# 7/17-\n18"). Codex review caught both directions after they were fixed
# one at a time in two separate commits.
_BARE_SEPARATOR_PATTERN = re.compile(r"[ \t]*[-~～至到]\s*")


def _find_range_pairs(
    text: str, matches: list[tuple[date, int, int]]
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for i in range(len(matches) - 1):
        between = text[matches[i][2] : matches[i + 1][1]]
        if _BARE_SEPARATOR_PATTERN.fullmatch(between):
            pairs.append((i, i + 1))
    return pairs


def _resolve_range_pairs(
    range_pairs: list[tuple[int, int]],
    date_matches: list[tuple[date, int, int]],
    labels: dict[int, str | None],
    unlabeled: dict[int, date],
    checkin: date | None,
    checkout: date | None,
) -> tuple[date | None, date | None]:
    # A range pair ("7/17-18", "8/20-8/22") is strong-enough evidence on its
    # own that if EITHER side already got a label (e.g. "7/17-18退房" labels
    # only the suffix as checkout), the other side fills the opposite, still-
    # empty role -- rather than sitting stranded in unlabeled_dates, unused,
    # because the plain "exactly two unlabeled dates" fallback below never
    # sees it. Flagged by Codex review of commit eec20a8 (P2).
    for lo_idx, hi_idx in range_pairs:
        if labels[lo_idx] == "checkin" and labels[hi_idx] is None and checkout is None:
            checkout = date_matches[hi_idx][0]
            unlabeled.pop(hi_idx, None)
        elif labels[hi_idx] == "checkout" and labels[lo_idx] is None and checkin is None:
            checkin = date_matches[lo_idx][0]
            unlabeled.pop(lo_idx, None)
        elif (
            labels[hi_idx] == "checkin"
            and labels[lo_idx] is None
            and checkin == date_matches[hi_idx][0]
            and checkout is None
        ):
            # A trailing 「入住」 after a full "A-B" range attaches, via
            # _has_close_label_after, to B (the later/closer date) even
            # though it scopes the whole range: "8/20-8/22入住" means check
            # in on 8/20 and check out on 8/22, not check in on 8/22. Undo
            # the main loop's earlier (wrong) checkin=B and re-point it to
            # the earlier date, with B becoming checkout.
            checkin = date_matches[lo_idx][0]
            checkout = date_matches[hi_idx][0]
            unlabeled.pop(lo_idx, None)
    return checkin, checkout


def _range_suffix_match(
    text: str, after: int, year: int, month: int, not_before: date | None
) -> list[tuple[date, int, int]]:
    # `year` is the year its parent date resolved to, not the base year: once
    # "7/17" in "7/17-18" has rolled to next July, its bare "18" belongs to
    # the same July.
    suffix = _RANGE_SEPARATOR_DAY_PATTERN.match(text, after)
    if suffix is None:
        return []
    parsed_date = _build_date(year, month, int(suffix.group("day")), not_before)
    if parsed_date is None:
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
