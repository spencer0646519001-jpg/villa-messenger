import re

from app.domain.number_parser import parse_chinese_or_arabic_number
from app.domain.parser_models import PetParseResult


_NUMBER_PATTERN = r"\d+|十[一二兩三四五六]?|[一二兩三四五六七八九十]"
_DOG_TERMS = ("毛小孩", "毛孩", "狗狗", "小狗", "狗")
_PET_TERMS = (*_DOG_TERMS, "寵物")
_PET_TERM_PATTERN = "|".join(_PET_TERMS)
_PET_COUNT_PATTERN = re.compile(
    rf"(?P<count>{_NUMBER_PATTERN})\s*(?:隻|只|個)?\s*(?P<pet>{_PET_TERM_PATTERN})"
)
# Contextual answer only: used when the state machine already knows it just
# asked "幾隻寵物?", so a bare "1隻" (no pet noun repeated) still counts --
# mirrors room_count_parser.parse_room_count_answer's anchored-answer style.
_PET_COUNT_ANSWER_PATTERN = re.compile(rf"^(?P<count>{_NUMBER_PATTERN})\s*(?:隻|只|個)?$")

_LABEL_NEGATION_TERMS = ("否", "沒有", "沒帶", "不需要", "不用", "不要", "不帶", "無")
# Bare "否" is deliberately EXCLUDED here (unlike _LABEL_NEGATION_TERMS above):
# a lot of form-field questions are phrased "是否有寵物" ("是否" = "whether"),
# which itself contains "否" right before "有寵物" -- using the same term list
# for this prefix-style check would misread the QUESTION as a "no pet" ANSWER,
# regardless of what the customer actually filled in after it.
_NATURAL_NEGATION_TERMS = ("沒有", "沒帶", "不需要", "不用", "不要", "不帶", "無")
# Gap between the "label:" separator and its answer: same line (just
# horizontal whitespace), OR the answer wrapped to the very next line (one
# newline). NOT bare \s* -- that would let the match skip past several blank
# lines/fields to grab an unrelated later line's leading word as "the answer".
_COLON_GAP = r"[:：][ \t]*\r?\n?[ \t]*"
# Labeled-field negation: "是否有寵物(...)：否" / "：\n否" -- the customer
# answered a form field, not asked a question. Allow a short gap (parenthetical
# notes) between the pet term and the "label:value" separator. Anchored on the
# separator, so bare "否" is safe here: it only counts right after the
# separator, never inside "是否" in the label/question part before it.
_PET_LABEL_NEGATION_PATTERN = re.compile(
    rf"(?:{_PET_TERM_PATTERN})[^\n:：]{{0,30}}{_COLON_GAP}(?:{'|'.join(_LABEL_NEGATION_TERMS)})"
)
# Natural word order: "沒有帶寵物" / "不需要寵物"
_PET_PREFIX_NEGATION_PATTERN = re.compile(
    rf"(?:{'|'.join(_NATURAL_NEGATION_TERMS)})[^\n]{{0,4}}(?:{_PET_TERM_PATTERN})"
)


def parse_pets(text: str) -> PetParseResult:
    count_match = _PET_COUNT_PATTERN.search(text)
    pet_count = None
    matched_pet_term = None
    if count_match is not None:
        pet_count = parse_chinese_or_arabic_number(count_match.group("count"))
        matched_pet_term = count_match.group("pet")

    if count_match is None and (
        _PET_LABEL_NEGATION_PATTERN.search(text) is not None
        or _PET_PREFIX_NEGATION_PATTERN.search(text) is not None
    ):
        return PetParseResult(
            has_pet=False,
            pet_count=None,
            pet_type=None,
            needs_pet_count_confirmation=False,
            mentioned=True,
        )

    mentioned_pet_terms = [term for term in _PET_TERMS if term in text]
    has_pet = count_match is not None or bool(mentioned_pet_terms)
    if matched_pet_term is None and mentioned_pet_terms:
        matched_pet_term = mentioned_pet_terms[0]

    return PetParseResult(
        has_pet=has_pet,
        pet_count=pet_count,
        pet_type="dog" if matched_pet_term in _DOG_TERMS else None,
        needs_pet_count_confirmation=has_pet and pet_count is None,
        mentioned=has_pet,
    )


def parse_pet_count_answer(text: str) -> int | None:
    """Parse a bare reply to "幾隻寵物?" (e.g. "1隻", "1", "一隻") -- only
    valid when the caller already knows this message is answering that
    specific question, since it has no pet-noun requirement of its own."""
    match = _PET_COUNT_ANSWER_PATTERN.match(text.strip())
    if match is None:
        return None
    return parse_chinese_or_arabic_number(match.group("count"))
