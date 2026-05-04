import re


_CHINESE_DIGITS = {
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


def parse_chinese_or_arabic_number(text: str) -> int | None:
    value = text.strip()
    if not value:
        return None

    if re.fullmatch(r"\d+", value):
        return int(value)

    if value in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[value]

    if value == "十":
        return 10

    if len(value) == 2 and value.startswith("十"):
        ones = _CHINESE_DIGITS.get(value[1])
        if ones is None:
            return None
        return 10 + ones

    return None
