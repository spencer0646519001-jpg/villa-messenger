"""
faq_matcher — STAGE D: recognize a tightly-whitelisted set of general questions
so the bot can answer them deterministically (NO LLM), instead of falling into
the silent "non-inquiry -> push owner only" bucket.

This is the recognition half only; it produces no text. It mirrors
inquiry_intent's `_contains_any` substring style and operates on the SAME
NFKC-normalized text the other parsers use (so full-width IME input matches).

Two tiers:
  - tier 1 (answerable): breakfast / checkout / pets -- the answer lives in the
    tenant config, so the bot can reply fully with no "已通知" claim.
  - tier 2 (confirm-and-defer): wifi / parking -- we have no config detail yet,
    so the bot confirms receipt and the route notifies the owner.

Whitelist is deliberately TIGHT (specific substrings, no over-broad single
characters) so it never hijacks a price/availability/booking inquiry. Intent
classification (price > availability > booking > faq) already runs first; this
matcher is only ever consulted for the faq bucket, but the conservative keywords
are a second line of defense.
"""

from typing import Literal

from pydantic import BaseModel

FaqTopic = Literal["breakfast", "checkout", "pets", "wifi", "parking", "whole_house", "amenities", "room_type", "location"]
FaqTier = Literal[1, 2]


class FaqMatch(BaseModel):
    topic: FaqTopic
    tier: FaqTier


# Ordered: first topic whose keyword appears wins. Each topic's keywords are
# specific enough not to collide across topics.
_TIER1_KEYWORDS: tuple[tuple[FaqTopic, tuple[str, ...]], ...] = (
    ("breakfast", ("早餐", "早飯")),
    ("checkout", ("退房", "退房時間", "幾點退", "checkout")),
    ("pets", ("寵物", "毛孩", "帶狗", "帶寵物")),
    ("wifi", ("wifi", "wi-fi", "網路", "無線網路")),
    ("parking", ("停車", "車位", "停車場")),
    ("whole_house", ("包棟", "整棟")),
    ("amenities", ("設備", "設施")),
    ("room_type", ("房型", "樓層", "幾間房", "幾人房")),
    ("location", ("地址", "位置", "怎麼去", "地點")),
)

_TIER2_KEYWORDS: tuple[tuple[FaqTopic, tuple[str, ...]], ...] = ()

# Informational topics that are NOT stage-C pricing line-items.  When match_faq
# hits one of these, the composer overrides the per-message price/availability
# intent and routes directly to the FAQ branch.
# NOTE: "checkout" is intentionally excluded — its keyword "退房" collides with
# the checkout-date slot in pricing inquiries (e.g. "5/14 退房 多少錢"),
# causing price-intent messages to be wrongly hijacked into a checkout-time
# answer.  Safe inclusion requires date-parsing to run before FAQ matching;
# that is a separate follow-up item.
NON_PRICEABLE: frozenset[FaqTopic] = frozenset({
    "breakfast", "pets", "wifi", "parking"
})


def match_faq(text: str) -> FaqMatch | None:
    """Return the whitelisted FAQ topic+tier this text hits, or None."""
    lowered = text.lower()  # 'wifi'/'checkout' keywords are ASCII; CJK unaffected.
    for topic, keywords in _TIER1_KEYWORDS:
        if _contains_any(lowered, keywords):
            return FaqMatch(topic=topic, tier=1)
    for topic, keywords in _TIER2_KEYWORDS:
        if _contains_any(lowered, keywords):
            return FaqMatch(topic=topic, tier=2)
    return None


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
