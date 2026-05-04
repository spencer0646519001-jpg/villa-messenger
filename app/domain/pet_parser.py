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


def parse_pets(text: str) -> PetParseResult:
    count_match = _PET_COUNT_PATTERN.search(text)
    pet_count = None
    matched_pet_term = None
    if count_match is not None:
        pet_count = parse_chinese_or_arabic_number(count_match.group("count"))
        matched_pet_term = count_match.group("pet")

    mentioned_pet_terms = [term for term in _PET_TERMS if term in text]
    has_pet = count_match is not None or bool(mentioned_pet_terms)
    if matched_pet_term is None and mentioned_pet_terms:
        matched_pet_term = mentioned_pet_terms[0]

    return PetParseResult(
        has_pet=has_pet,
        pet_count=pet_count,
        pet_type="dog" if matched_pet_term in _DOG_TERMS else None,
        needs_pet_count_confirmation=has_pet and pet_count is None,
    )
