import pytest

from app.domain.urgency_detector import (
    URGENCY_KEYWORDS,
    UrgencyDetectionResult,
    detect_urgency,
)


# --- Basic behavior --------------------------------------------------------

def test_empty_string_is_not_urgent():
    result = detect_urgency("")
    assert result.is_urgent is False
    assert result.matched_keywords == []
    assert result.category is None


def test_whitespace_only_is_not_urgent():
    result = detect_urgency("   ")
    assert result.is_urgent is False
    assert result.matched_keywords == []
    assert result.category is None


def test_plain_inquiry_room_availability_is_not_urgent():
    result = detect_urgency("請問還有空房嗎?")
    assert result.is_urgent is False
    assert result.matched_keywords == []
    assert result.category is None


def test_plain_inquiry_pricing_is_not_urgent():
    result = detect_urgency("5/12 入住 5/14 退房 4 大人多少錢?")
    assert result.is_urgent is False
    assert result.matched_keywords == []
    assert result.category is None


# --- Single-keyword matches per category ----------------------------------

@pytest.mark.parametrize(
    "text, expected_category, expected_keywords",
    [
        ("廚房沒水了",       "water",     ["沒水"]),
        ("瓦斯味好重",       "gas",       ["瓦斯味"]),
        ("整個房間停電",     "electric",  ["停電"]),
        ("冷氣壞了好熱",     "hvac",      ["冷氣壞"]),
        ("馬桶堵住了怎麼辦", "toilet",    ["馬桶堵"]),
        ("鎖頭壞了進不去",   "door",      ["鎖頭壞"]),
        ("好像有小偷",       "safety",    ["小偷"]),
        ("沒熱水洗澡",       "hot_water", ["沒熱水"]),
    ],
)
def test_single_keyword_per_category(text, expected_category, expected_keywords):
    result = detect_urgency(text)
    assert result.is_urgent is True
    assert result.category == expected_category
    assert result.matched_keywords == expected_keywords


# --- Priority resolution when multiple categories match -------------------

def test_priority_gas_before_water():
    result = detect_urgency("瓦斯漏然後也沒水")
    assert result.is_urgent is True
    assert result.category == "gas"
    assert "瓦斯漏" in result.matched_keywords
    assert "沒水" in result.matched_keywords


def test_priority_safety_is_highest():
    result = detect_urgency("起火了沒電也沒水")
    assert result.is_urgent is True
    assert result.category == "safety"
    assert "起火" in result.matched_keywords
    assert "沒電" in result.matched_keywords
    assert "沒水" in result.matched_keywords


# --- Duplicate keyword deduplication --------------------------------------

def test_duplicate_keywords_deduplicated():
    result = detect_urgency("沒水真的沒水拜託快來")
    assert result.is_urgent is True
    assert result.matched_keywords == ["沒水"]
    assert result.category == "water"


# --- Multiple keywords same category --------------------------------------

def test_multiple_keywords_same_category():
    result = detect_urgency("停水又漏水")
    assert result.is_urgent is True
    assert result.category == "water"
    assert "停水" in result.matched_keywords
    assert "漏水" in result.matched_keywords
    assert len(result.matched_keywords) == 2


# --- Keyword as substring of a larger word --------------------------------

def test_keyword_substring_停水中():
    result = detect_urgency("停水中")
    assert result.is_urgent is True
    assert result.matched_keywords == ["停水"]


def test_keyword_substring_沒水嗎():
    result = detect_urgency("沒水嗎")
    assert result.is_urgent is True
    assert result.matched_keywords == ["沒水"]


# --- Non-urgent messages that contain partial keywords --------------------

def test_停車場_is_not_urgent():
    result = detect_urgency("停車場在哪")
    assert result.is_urgent is False
    assert result.matched_keywords == []
    assert result.category is None


def test_水費_is_not_urgent():
    result = detect_urgency("水費怎麼算")
    assert result.is_urgent is False
    assert result.matched_keywords == []
    assert result.category is None


def test_鎖匙_is_not_urgent():
    result = detect_urgency("鎖匙在哪領")
    assert result.is_urgent is False
    assert result.matched_keywords == []
    assert result.category is None


# --- Edge: keyword at very beginning or end -------------------------------

def test_keyword_alone():
    result = detect_urgency("沒水")
    assert result.is_urgent is True
    assert result.matched_keywords == ["沒水"]
    assert result.category == "water"


def test_keyword_at_end():
    result = detect_urgency("我家沒水")
    assert result.is_urgent is True
    assert "沒水" in result.matched_keywords


def test_keyword_with_punctuation():
    result = detect_urgency("沒水。")
    assert result.is_urgent is True
    assert "沒水" in result.matched_keywords


# --- Model invariants -----------------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", "請問還有空房嗎?", "停車場在哪", "水費怎麼算"])
def test_invariant_not_urgent_has_empty_matched_and_none_category(text):
    result = detect_urgency(text)
    assert result.is_urgent is False
    assert result.matched_keywords == []
    assert result.category is None


@pytest.mark.parametrize(
    "text",
    [
        "廚房沒水了",
        "瓦斯味好重",
        "起火了",
        "馬桶堵住了",
        "好像有小偷",
        "瓦斯漏然後也沒水",
    ],
)
def test_invariant_urgent_has_nonempty_matched_and_category(text):
    result = detect_urgency(text)
    assert result.is_urgent is True
    assert len(result.matched_keywords) >= 1
    assert result.category is not None


# --- Snapshot test for keyword categories ---------------------------------

def test_urgency_keywords_has_expected_categories():
    expected = {"water", "electric", "gas", "hot_water", "hvac", "door", "toilet", "safety"}
    assert set(URGENCY_KEYWORDS.keys()) == expected


def test_urgency_keywords_every_category_non_empty():
    for category, keywords in URGENCY_KEYWORDS.items():
        assert len(keywords) > 0, f"category {category!r} has no keywords"


# --- Return type sanity ---------------------------------------------------

def test_returns_pydantic_model():
    result = detect_urgency("沒水")
    assert isinstance(result, UrgencyDetectionResult)
