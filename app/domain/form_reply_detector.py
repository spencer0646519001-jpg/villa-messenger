from app.domain.date_parser import parse_stay_dates
from app.domain.guest_count_parser import parse_guest_counts

_MIN_LABELED_LINES = 3
_LABEL_SEPARATORS = (":", "：")
_MIN_UNLABELED_LINES = 3
_MAX_UNLABELED_LINE_CHARS = 20
_QUESTION_MARKERS = ("?", "？", "請問", "想問", "嗎")


def looks_like_structured_form_reply(text: str) -> bool:
    """True when text looks like a filled-in multi-field intake form -- either
    labeled ("是否有寵物:否") or unlabeled sequential answers (name/phone/dates/
    headcount/pet-status each retyped free-hand on its own short line, no field
    labels) -- rather than a question, PLUS a real date or guest-count signal.
    i.e. the customer filled in a multi-field intake form (e.g. a LINE OA
    auto-reply asking for contact/date/headcount/pets/...) rather than asking a
    question. Used to keep such replies out of FAQ-keyword routing, since a
    filled-in field like "是否有寵物:否" (or a bare "無寵物" line in the
    unlabeled form) contains an FAQ keyword but is an answer, not an inquiry."""
    dates = parse_stay_dates(text)
    guests = parse_guest_counts(text)
    if dates.checkin_date is None and guests.guest_count is None:
        return False
    if _labeled_line_count(text) >= _MIN_LABELED_LINES:
        return True
    return _unlabeled_answer_line_count(text) >= _MIN_UNLABELED_LINES


def _labeled_line_count(text: str) -> int:
    count = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for sep in _LABEL_SEPARATORS:
            if sep not in line:
                continue
            label, _, value = line.partition(sep)
            if label.strip() and value.strip():
                count += 1
            break
    return count


def _unlabeled_answer_line_count(text: str) -> int:
    """Counts short, non-question lines -- the shape of a customer filling in
    several discrete answers one per line without the field labels (e.g. a
    LINE OA intake form retyped free-hand: name / phone / dates / headcount /
    pet-status, each its own short line). A line long enough to read as a real
    sentence, or carrying a question marker, is excluded from the count so a
    genuine question sent across a few short lines is not misread as a form."""
    count = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > _MAX_UNLABELED_LINE_CHARS:
            continue
        if any(marker in line for marker in _QUESTION_MARKERS):
            continue
        count += 1
    return count
