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
_NATURAL_NEGATION_TERMS = ("不要", "不用", "不需要", "沒有", "無")
_NATURAL_AFFIRM_TERMS = ("要", "需要", "有")
# "想" (real customer phrasing: "想加烤肉" / "想烤肉") is deliberately its OWN
# pair with a much tighter gap than the _NATURAL_* terms above, not just
# added to that list -- Codex review of the first version of this fix (P1):
# with the shared 4-char gap, "想問一下烤肉費用" (asking about the FEE, not
# requesting BBQ) also matched and got wrongly persisted as wants_bbq=True,
# and "不太想烤肉" (a modified negation -- "不想" isn't a contiguous
# substring, so it slipped past _NATURAL_NEGATION_TERMS entirely) then hit
# the affirm side. A 2-char gap still covers every real reported phrasing
# ("想加烤肉", "想烤肉", "想要烤肉") while excluding "問一下"-style detours,
# and the negation pattern independently re-checks for a negation marker
# ahead of "想" within a small window so modifiers like "不太想" are caught
# without having to enumerate every possible modifier word.
_BBQ_WANT_NEGATION_MARKERS = ("不", "沒", "別", "無")
_BBQ_WANT_NEGATION_PATTERN = re.compile(
    rf"(?:{'|'.join(_BBQ_WANT_NEGATION_MARKERS)})[^\n]{{0,3}}想[^\n]{{0,2}}(?:{_BBQ_TERM_PATTERN})"
)
_BBQ_WANT_AFFIRM_PATTERN = re.compile(rf"想[^\n]{{0,2}}(?:{_BBQ_TERM_PATTERN})")

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
    # Both negation checks (natural order + the tighter "想" form) run before
    # either affirm check: "不想要烤肉" contains "要烤肉" as a bare substring,
    # which would otherwise let the generic PREFIX_AFFIRM check fire first
    # and misread an explicit decline as a request.
    if _BBQ_PREFIX_NEGATION_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=False, mentioned=True)
    if _BBQ_WANT_NEGATION_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=False, mentioned=True)
    if _BBQ_PREFIX_AFFIRM_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=True, mentioned=True)
    if _BBQ_WANT_AFFIRM_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=True, mentioned=True)
    # No recognizable affirm/negation pattern matched -- e.g. the bare form
    # question "是否烤肉(...)" with no answer filled in yet, or an unrelated
    # sentence that happens to contain "烤肉". Neither is an explicit answer,
    # so mentioned stays False: this turn must NOT clear an existing wants_bbq
    # flag just because the BBQ term appears somewhere in the text.
    return BbqParseResult(wants_bbq=False, mentioned=False)
