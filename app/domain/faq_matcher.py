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

FaqTopic = Literal["breakfast", "checkout", "pets", "wifi", "parking", "whole_house", "amenities", "bbq", "deposit", "room_type", "location"]
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
    ("bbq", ("烤肉", "BBQ", "bbq")),
    ("deposit", ("訂金", "押金", "保證金", "定金")),
    ("room_type", ("房型", "樓層", "幾間房", "幾人房")),
    # "在哪"/"哪裡" alone are unrestricted interrogatives ("附近哪裡有便利
    # 商店" is asking about a NEARBY convenience store, not the property's
    # own location) -- Codex review of an earlier version of this fix.
    # Scoped to phrases that specifically ask where the PROPERTY/host is,
    # matching this file's existing style of compound phrases (e.g.
    # "怎麼去") rather than bare interrogative words.
    ("location", ("地址", "位置", "怎麼去", "地點", "你們在哪", "你們家在哪", "民宿在哪")),
)

_TIER2_KEYWORDS: tuple[tuple[FaqTopic, tuple[str, ...]], ...] = ()

# Booking-equivalent topics describe the lodging product / booking action itself,
# rather than an ancillary policy.  A customer who supplies a date or guest count
# while asking about 包棟 is normally asking whether the whole-house stay can be
# booked, not what "whole house" means.  Keep this classification centralized so
# the intent classifier and final reply composer cannot drift apart.
_BOOKING_EQUIVALENT_TOPICS: frozenset[FaqTopic] = frozenset({"whole_house"})

# Informational topics that are NOT stage-C pricing line-items.  When match_faq
# hits one of these, the composer overrides the per-message price/availability
# intent and routes directly to the FAQ branch.
# NOTE: "checkout" is intentionally excluded — its keyword "退房" collides with
# the checkout-date slot in pricing inquiries (e.g. "5/14 退房 多少錢"),
# causing price-intent messages to be wrongly hijacked into a checkout-time
# answer.  Safe inclusion requires date-parsing to run before FAQ matching;
# that is a separate follow-up item.
NON_PRICEABLE: frozenset[FaqTopic] = frozenset({
    "breakfast", "pets", "wifi", "parking", "bbq", "deposit"
})


def match_faq(text: str) -> FaqMatch | None:
    """Return the whitelisted FAQ topic+tier this text hits, or None."""
    matches = match_all_faq_topics(text)
    return matches[0] if matches else None


def match_all_faq_topics(text: str) -> tuple[FaqMatch, ...]:
    """Return every FAQ topic hit, preserving the existing topic priority order."""
    lowered = text.lower()  # 'wifi'/'checkout' keywords are ASCII; CJK unaffected.
    matches: list[FaqMatch] = []
    for topic, keywords in _TIER1_KEYWORDS:
        if _contains_any(lowered, keywords):
            matches.append(FaqMatch(topic=topic, tier=1))
    for topic, keywords in _TIER2_KEYWORDS:
        if _contains_any(lowered, keywords):
            matches.append(FaqMatch(topic=topic, tier=2))
    return tuple(matches)


def is_booking_equivalent_topic(topic: str) -> bool:
    """True when a topic names the bookable lodging product/action itself."""
    return topic in _BOOKING_EQUIVALENT_TOPICS


def has_explicit_faq_topic(text: str) -> bool:
    """True when text hits the FAQ topic keyword whitelist."""
    return match_faq(text) is not None


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
