from app.domain.date_parser import parse_stay_dates
from app.domain.guest_count_parser import parse_guest_counts

_MIN_LABELED_LINES = 3
_LABEL_SEPARATORS = (":", "：")


def looks_like_structured_form_reply(text: str) -> bool:
    """True when text has 3+ 'label:value' lines AND a real date or guest-count
    signal -- i.e. the customer filled in a multi-field intake form (e.g. a LINE
    OA auto-reply asking for contact/date/headcount/pets/...) rather than asking
    a question. Used to keep such replies out of FAQ-keyword routing, since a
    filled-in field like "是否有寵物:否" contains an FAQ keyword but is an answer,
    not an inquiry."""
    if _labeled_line_count(text) < _MIN_LABELED_LINES:
        return False
    dates = parse_stay_dates(text)
    guests = parse_guest_counts(text)
    return dates.checkin_date is not None or guests.guest_count is not None


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
