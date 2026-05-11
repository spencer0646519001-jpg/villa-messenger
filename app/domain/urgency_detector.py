"""
Urgency detection for V1.5.

Pure function. No I/O. No state.

When detect_urgency returns is_urgent=True, the calling service (PR7
InquiryService) MUST:
  - Not auto-reply to the customer
  - Push the original message to the tenant owner regardless of OperationMode
  - Log the message with urgency category

This enforcement does NOT live in this module — only detection does.
"""

from pydantic import BaseModel, Field


# Category ordering matters for category-resolution priority when multiple
# categories match. List safety-related first.
URGENCY_KEYWORDS: dict[str, list[str]] = {
    "safety":     ["小偷", "闖入", "受傷", "火災", "起火", "冒煙"],
    "gas":        ["瓦斯味", "瓦斯漏", "漏氣"],
    "water":      ["沒水", "停水", "漏水", "淹水"],
    "electric":   ["停電", "跳電", "沒電"],
    "hot_water":  ["沒熱水", "熱水器壞"],
    "hvac":       ["冷氣壞", "冷氣不冷", "暖氣壞"],
    "door":       ["鎖頭壞", "開不了門", "鑰匙壞", "鑰匙不見"],
    "toilet":     ["馬桶堵", "馬桶不通", "馬桶塞住"],
}


class UrgencyDetectionResult(BaseModel):
    is_urgent: bool
    matched_keywords: list[str] = Field(default_factory=list)
    category: str | None = None
    # When multiple categories match, `category` is the FIRST one matched
    # in URGENCY_KEYWORDS iteration order (safety > gas > water > ...).
    # matched_keywords contains ALL matches across all categories.


def detect_urgency(text: str) -> UrgencyDetectionResult:
    """
    Scan input text for urgency keywords.

    Behavior:
      - Empty / whitespace-only text → not urgent.
      - Substring matching (case-sensitive — keywords are Chinese, no case).
      - All matched keywords are returned, but `category` reports only
        the FIRST category that matched (priority order in URGENCY_KEYWORDS).
      - Duplicate keywords (same keyword appearing twice in text) are
        deduplicated in matched_keywords.

    Args:
        text: The customer's raw message text.

    Returns:
        UrgencyDetectionResult with is_urgent set based on any keyword match.
    """
    if not text or not text.strip():
        return UrgencyDetectionResult(is_urgent=False, matched_keywords=[], category=None)

    matched: list[str] = []
    first_category: str | None = None

    for category, keywords in URGENCY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text and keyword not in matched:
                matched.append(keyword)
                if first_category is None:
                    first_category = category

    if not matched:
        return UrgencyDetectionResult(is_urgent=False, matched_keywords=[], category=None)

    return UrgencyDetectionResult(
        is_urgent=True,
        matched_keywords=matched,
        category=first_category,
    )
