import re


_ARABIC_ROOM_PATTERNS = (
    re.compile(r"(?P<count>\d+)\s*房"),
    re.compile(r"(?P<count>\d+)\s*間(?:\s*房)?"),
    re.compile(r"開\s*(?P<count>\d+)(?:\s*(?:房|間(?:\s*房)?))?"),
)
_ZH_ROOM_PATTERNS = (
    re.compile(r"(?P<count>[一二兩三四五六七八九十]+)\s*(?:房|間(?:\s*房)?)"),
    re.compile(r"開\s*(?P<count>[一二兩三四五六七八九十]+)(?:\s*(?:房|間(?:\s*房)?))?"),
)
_ROOM_COUNT_ANSWER_ARABIC = re.compile(r"^(?:[開开]\s*)?(?P<count>\d+)$")
_ROOM_COUNT_ANSWER_ZH = re.compile(r"^(?:[開开]\s*)?(?P<count>[一二兩三四五六七八九十]+)$")
_ZH_DIGITS = {
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def parse_room_count(text: str) -> int | None:
    for pattern in _ARABIC_ROOM_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return int(match.group("count"))
    for pattern in _ZH_ROOM_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return _parse_zh_count(match.group("count"))
    return None


def parse_room_count_answer(text: str) -> int | None:
    value = text.strip()
    match = _ROOM_COUNT_ANSWER_ARABIC.match(value)
    if match is not None:
        return int(match.group("count"))
    match = _ROOM_COUNT_ANSWER_ZH.match(value)
    if match is not None:
        return _parse_zh_count(match.group("count"))
    return None


def _parse_zh_count(value: str) -> int | None:
    if value == "十":
        return 10
    if value.startswith("十") and len(value) == 2:
        ones = _ZH_DIGITS.get(value[1:])
        return 10 + ones if ones is not None else None
    if value.endswith("十") and len(value) == 2:
        tens = _ZH_DIGITS.get(value[:1])
        return tens * 10 if tens is not None else None
    if "十" in value and len(value) == 3:
        tens = _ZH_DIGITS.get(value[:1])
        ones = _ZH_DIGITS.get(value[2:])
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return _ZH_DIGITS.get(value)


# The whole message is one room count and nothing else ("4", "開3房", "四間房").
# Used while the open state is waiting for a room count: anything that does NOT
# match this is sent to the LLM instead of trusting parse_room_count, which
# confidently misreads answers like "4人2間 2人2間" (first "2間" -> 2 rooms).
_PLAIN_ROOM_COUNT_ANSWER = re.compile(
    r"^(?:[開开]\s*)?(?P<count>\d+|[一二兩三四五六七八九十]+)\s*(?:間\s*房|間|房)?\s*(?:就好|好了)?$"
)
_ROOM_MENTION = re.compile(r"(?:\d+|[一二兩三四五六七八九十]+)\s*(?:間|房)")


def parse_plain_room_count_answer(text: str) -> int | None:
    match = _PLAIN_ROOM_COUNT_ANSWER.match(text.strip())
    if match is None:
        return None
    value = match.group("count")
    return int(value) if value.isdigit() else _parse_zh_count(value)


def count_room_mentions(text: str) -> int:
    return len(_ROOM_MENTION.findall(text))
