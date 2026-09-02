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
# "不想"/"不太想" are literal terms here (not a generic "negation marker +
# arbitrary gap + 想" scan) -- Codex review of an earlier version of this
# fix found that a marker-proximity heuristic cuts both ways wrong: too
# TIGHT a post-想 gap misses a real decline with an extra verb ("不想要參加
# 烤肉"), while a wide-enough gap to catch that also false-positives on
# discourse markers that have nothing to do with negating the request
# ("沒問題，想加烤肉", "不過想加烤肉" are both AFFIRMATIVE). Literal terms
# reuse the same proven gap-based BBQ-term matching as every other term in
# this list, with no separate mechanism to keep in sync.
# "沒想" alongside "不想"/"不太想" -- Codex review: "沒想要烤肉" is the same
# decline as "不想要烤肉", just with "沒" instead of "不".
#
# These three are checked with a negative lookahead against "到" right after
# "想" (see _WANT_TERM_PATTERN below) -- Codex review: "沒想到原來烤肉要收費"
# ("didn't expect BBQ costs extra", not a decline) matched as a bare "沒想"
# negation, since "到原來" fits the negation gap. "想到" always means "think
# of/realize" in Chinese, never "want to", so excluding it is categorical
# and safe, not a narrow patch for this one sentence.
_WANT_NEGATION_TERMS = ("不想", "不太想", "沒想")
_OTHER_NEGATION_TERMS = ("不要", "不用", "不需要", "沒有", "無")
_WANT_TERM_PATTERN = "|".join(_WANT_NEGATION_TERMS)
# Bare "想" (real customer phrasing: "想加烤肉" / "想烤肉") stays its own
# pair with a much tighter gap than the natural-word-order affirm terms below
# -- with the shared 4-char gap, "想問一下烤肉費用" (asking about the FEE, not requesting BBQ)
# also matched and got wrongly persisted as wants_bbq=True. A 2-char gap
# still covers every real reported phrasing ("想加烤肉", "想烤肉", "想要烤肉")
# while excluding "問一下"-style detours. No separate negation pattern is
# needed for it: an actual decline is always caught by the negation pattern
# below (checked first) via the literal "不想"/"不太想"/"沒想" terms.
_BBQ_WANT_AFFIRM_PATTERN = re.compile(rf"想(?!到)[^\n]{{0,2}}(?:{_BBQ_TERM_PATTERN})")

# "想到" ("think of" / "realize", never "want to") heads a short remark that
# can contain other trigger words with nothing to do with a BBQ request --
# Codex review: "沒想到要收烤肉費" ("didn't expect there'd be a BBQ fee")
# wasn't read as a "沒想" negation (correctly guarded above), but the "要"
# inside "要收烤肉費" then matched the unrelated PREFIX_AFFIRM pattern
# instead, turning a surprised remark about pricing into a persisted
# request. Rather than guard every trigger word individually against every
# "想到" remark, the remark itself is stripped out before any pattern below
# ever sees it -- but bounded to a SHORT window (0-6 chars, stopping early
# at clause punctuation), not the unbounded rest of the string: Codex review
# of the first version of this fix found that customers routinely drop
# punctuation entirely, so an unbounded strip deleted a genuinely separate,
# later request in the SAME run-on sentence ("沒想到可以烤肉但我想烤肉" --
# the later "我想烤肉" got erased along with the earlier remark just because
# nothing marked where the remark ended). (?!時候) excludes "想到時候" ("when
# the time comes") -- a distinct, common construction where "想" and "到" are
# NOT the "think of/realize" compound at all ("我們想到時候要烤肉" is a
# genuine future want, not a remark about an unexpected fact).
_THINK_OF_CLAUSE = re.compile(r"(?:不太|不|沒)?想到(?!時候)[^\n，,。！？；;]{0,6}")

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
# Natural word order: "不用烤肉" / "不需要BBQ" / "要烤肉". Both the negation
# and affirm gap exclude CLAUSE-separating punctuation (，,。！？；;) -- Codex
# review: "不想加床，要烤肉" declines an EXTRA BED, not BBQ, but the
# unrestricted 4-char gap needed to reach "不想要參加烤肉" also reached
# straight through the comma into a separate, genuinely affirmative clause
# and wrongly negated it. Symmetrically, once the negation gap stopped at
# the comma, "不需要加床，烤肉也不用" (BOTH declined) started matching the
# AFFIRM pattern instead -- the bare "要" trapped inside "不需要" could still
# cross the same comma via the (still-unrestricted) affirm gap, so the
# exclusion has to apply to both patterns to stay symmetric. "、" (a list
# separator for coordinated items under ONE verb, not a new clause) is
# deliberately NOT excluded outright -- "不要加床、烤肉" declines BOTH listed
# items, and excluding "、" broke exactly that by blocking the negation from
# reaching past it while the affirm pattern still could. But "、" can ALSO
# introduce a genuinely separate predicate with its own verb ("不要加床、要
# 烤肉" = don't want a bed, [but do] want BBQ) -- Codex review found the
# negation gap crossing "、" into a fresh affirm-trigger word ("要") and
# wrongly claiming the BBQ term for the negation on the OTHER side of that
# new predicate. _NEGATION_GAP (negation-only, not shared with affirm below)
# rejects consuming "、" when specifically "要"/"需要" immediately follows it
# -- those two carry a strong "switching to affirmative" signal paired
# against "不要"/"不需要". "有" is deliberately EXCLUDED from this block list
# -- Codex review: "不要有煙、有烤肉味" (no smoke, no BBQ smell -- BOTH still
# declined) repeats "有" for each item in a single negated enumeration, a
# common parallel-list construction, not a switch to a new predicate the way
# "要" signals.
_CLAUSE_GAP = r"[^\n，,。！？；;]{0,4}"
_NEGATION_BLOCKED_AFTER_LIST_SEPARATOR = ("要", "需要")
_NEGATION_GAP = (
    rf"(?:(?!、(?:{'|'.join(_NEGATION_BLOCKED_AFTER_LIST_SEPARATOR)}))"
    rf"[^\n，,。！？；;]){{0,4}}"
)
_BBQ_PREFIX_NEGATION_PATTERN = re.compile(
    rf"(?:{_WANT_TERM_PATTERN}(?!到)|{'|'.join(_OTHER_NEGATION_TERMS)}){_NEGATION_GAP}(?:{_BBQ_TERM_PATTERN})"
)
# Bare "有" is EXCLUDED when immediately followed by "人" -- "有人說烤肉不錯
# 欸" ("someone said your BBQ is good") is the existential-subject "有人"
# (someone/there is a person), not an affirmative "有[BBQ]" statement; the
# person doing the talking is a third party, not the customer stating a
# want. Real E2E regression: this previously matched and persisted
# wants_bbq=True from pure hearsay, even with an active booking state open.
_BBQ_PREFIX_AFFIRM_PATTERN = re.compile(
    rf"(?:要|需要|有(?!人)){_CLAUSE_GAP}(?:{_BBQ_TERM_PATTERN})"
)


def parse_bbq(text: str) -> BbqParseResult:
    text = _THINK_OF_CLAUSE.sub("", text)
    if _BBQ_LABEL_NEGATION_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=False, mentioned=True)
    if _BBQ_LABEL_AFFIRM_PATTERN.search(text) is not None:
        return BbqParseResult(wants_bbq=True, mentioned=True)
    if _BBQ_PREFIX_NEGATION_PATTERN.search(text) is not None:
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
