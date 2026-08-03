import re

from app.domain.parser_models import BbqParseResult

_BBQ_TERMS = ("烤肉", "BBQ", "bbq")
_BBQ_TERM_PATTERN = "|".join(_BBQ_TERMS)

_AFFIRM_TERMS = ("是", "要", "需要", "有")
# Bare "是"/"否" are deliberately EXCLUDED from the natural-order (prefix)
# checks below -- "是否烤肉" ("是否" = "whether") contains BOTH "是" and,
# right after it, "否", immediately before the BBQ term. Either bare
# character would misread the QUESTION itself as an ANSWER (affirmative or
# negative) regardless of what the customer actually filled in. Bare "是"/
# "否" are only safe in the colon-anchored _BBQ_LABEL_* patterns below, where
# they can only match right after the "label:" separator, never inside the
# "是否" question wording that precedes it.
_LABEL_NEGATION_TERMS = ("否", "不用", "不需要", "沒有", "無")
_NATURAL_NEGATION_TERMS = ("不用", "不需要", "沒有", "無")
_NATURAL_AFFIRM_TERMS = ("要", "需要", "有")

# Gap between the "label:" separator and its answer: same line (just
# horizontal whitespace), OR the answer wrapped to the very next line (one
# newline). NOT bare \s* -- that would let the match skip past several blank
# lines/fields to grab an unrelated later line's leading word as "the answer".
_COLON_GAP = r"[:：][ \t]*\r?\n?[ \t]*"
# Labeled-field answer: "是否烤肉(...)：是" / "：否" / "：\n是" -- anchored on
# the "label:value" separator, so bare "是"/"否" are safe here (they only
# count right after the separator, never inside "是否" in the question part
# before it).
_BBQ_LABEL_AFFIRM_PATTERN = re.compile(
    rf"(?:{_BBQ_TERM_PATTERN})[^\n:：]{{0,30}}{_COLON_GAP}(?:{'|'.join(_AFFIRM_TERMS)})"
)
_BBQ_LABEL_NEGATION_PATTERN = re.compile(
    rf"(?:{_BBQ_TERM_PATTERN})[^\n:：]{{0,30}}{_COLON_GAP}(?:{'|'.join(_LABEL_NEGATION_TERMS)})"
)
# Natural word order: "不用烤肉" / "不需要BBQ" / "要烤肉"
_BBQ_PREFIX_NEGATION_PATTERN = re.compile(
    rf"(?:{'|'.join(_NATURAL_NEGATION_TERMS)})[^\n]{{0,4}}(?:{_BBQ_TERM_PATTERN})"
)
_BBQ_PREFIX_AFFIRM_PATTERN = re.compile(
    rf"(?:{'|'.join(_NATURAL_AFFIRM_TERMS)})[^\n]{{0,4}}(?:{_BBQ_TERM_PATTERN})"
)


def parse_bbq(text: str) -> BbqParseResult:
    if _BBQ_LABEL_NEGATION_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=False)
    if _BBQ_LABEL_AFFIRM_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=True)
    if _BBQ_PREFIX_NEGATION_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=False)
    if _BBQ_PREFIX_AFFIRM_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=True)
    return BbqParseResult(wants_bbq=False)
