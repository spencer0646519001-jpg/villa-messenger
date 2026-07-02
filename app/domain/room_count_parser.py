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
