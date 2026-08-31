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
_LABEL_NEGATION_TERMS = ("否", "不要", "不用", "不需要", "沒有", "無")
# "不想" sits alongside "不要"/"不用" here (natural-order only, not the
# colon-anchored label list) precisely because "想" is now a natural-order
# affirm term below -- without it, "不想烤肉" would fail every negation check
# and then match the "想...烤肉" affirm pattern, flipping an explicit decline
# into wants_bbq=True.
_NATURAL_NEGATION_TERMS = ("不要", "不用", "不需要", "沒有", "無", "不想")
# "想" added for real customer phrasing like "想加烤肉" / "想烤肉" -- a plain
# BBQ request ("我要訂房，想加烤肉") was falling through to mentioned=False
# because "要" in "我要訂房" sits too far from "烤肉" to match, and "想" itself
# wasn't recognized as a request word at all, so wants_bbq was never
# persisted even though the FAQ topic matcher (keyword-only, see
# faq_matcher.py) separately fired on the bare word "烤肉" and answered with
# the BBQ policy -- making it look like the system "knew" but silently
# dropped it.
_NATURAL_AFFIRM_TERMS = ("要", "需要", "有", "想")

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
        return BbqParseResult(wants_bbq=False, mentioned=True)
    if _BBQ_LABEL_AFFIRM_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=True, mentioned=True)
    if _BBQ_PREFIX_NEGATION_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=False, mentioned=True)
    if _BBQ_PREFIX_AFFIRM_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=True, mentioned=True)
    # No recognizable affirm/negation pattern matched -- e.g. the bare form
    # question "是否烤肉(...)" with no answer filled in yet, or an unrelated
    # sentence that happens to contain "烤肉". Neither is an explicit answer,
    # so mentioned stays False: this turn must NOT clear an existing wants_bbq
    # flag just because the BBQ term appears somewhere in the text.
    return BbqParseResult(wants_bbq=False, mentioned=False)
