"""
Unit tests for faq_matcher (STAGE D whitelist recognition). The matcher is the
recognition half only -- it returns a topic+tier or None, no text.
"""

import pytest

from app.domain.faq_matcher import (
    is_booking_equivalent_topic,
    match_all_faq_topics,
    match_faq,
)
from app.domain.text_normalizer import normalize_for_parsing


# ---- tier 1 (answerable from config) --------------------------------------


@pytest.mark.parametrize("text", ["有早餐嗎", "請問早餐", "有附早飯嗎"])
def test_breakfast_is_tier1(text: str) -> None:
    match = match_faq(text)
    assert match is not None
    assert match.topic == "breakfast"
    assert match.tier == 1


@pytest.mark.parametrize("text", ["幾點退房", "退房時間", "幾點退", "what time is checkout"])
def test_checkout_is_tier1(text: str) -> None:
    match = match_faq(text)
    assert match is not None
    assert match.topic == "checkout"
    assert match.tier == 1


@pytest.mark.parametrize("text", ["可以帶寵物嗎", "毛孩可以嗎", "可以帶狗嗎", "想帶寵物"])
def test_pets_is_tier1(text: str) -> None:
    match = match_faq(text)
    assert match is not None
    assert match.topic == "pets"
    assert match.tier == 1


@pytest.mark.parametrize("text", ["有wifi嗎", "有 Wi-Fi 嗎", "有網路嗎", "有無線網路嗎"])
def test_wifi_is_tier1(text: str) -> None:
    match = match_faq(text)
    assert match is not None
    assert match.topic == "wifi"
    assert match.tier == 1


@pytest.mark.parametrize("text", ["有停車位嗎", "可以停車嗎", "有車位嗎", "有停車場嗎"])
def test_parking_is_tier1(text: str) -> None:
    match = match_faq(text)
    assert match is not None
    assert match.topic == "parking"
    assert match.tier == 1


@pytest.mark.parametrize("text", ["是包棟嗎", "可以包棟嗎", "整棟租嗎"])
def test_whole_house_is_tier1(text: str) -> None:
    match = match_faq(text)
    assert match is not None
    assert match.topic == "whole_house"
    assert match.tier == 1


def test_only_whole_house_is_booking_equivalent() -> None:
    assert is_booking_equivalent_topic("whole_house") is True
    for topic in (
        "breakfast", "checkout", "pets", "wifi", "parking", "amenities",
        "bbq", "deposit", "room_type", "location",
    ):
        assert is_booking_equivalent_topic(topic) is False


def test_all_topic_match_preserves_existing_first_match_priority() -> None:
    text = "8/15 包棟可以帶寵物嗎 9人"
    matches = match_all_faq_topics(text)

    assert [match.topic for match in matches] == ["pets", "whole_house"]
    assert match_faq(text).topic == "pets"


def test_bbq_is_tier1() -> None:
    match = match_faq("可以烤肉嗎")
    assert match is not None
    assert match.topic == "bbq"
    assert match.tier == 1


# ---- non-whitelist + collision guard --------------------------------------


@pytest.mark.parametrize("text", ["附近有什麼景點", "有附近景點嗎", "謝謝"])
def test_non_whitelist_returns_none(text: str) -> None:
    assert match_faq(text) is None


def test_price_word_collision_still_matches_topic_but_intent_gate_protects_quote() -> None:
    # match_faq is intentionally substring-based, so "早餐多少錢嗎" DOES hit
    # breakfast here -- the no-hijack guarantee is enforced upstream by keying
    # the composer's FAQ branch on inquiry_intent=="faq" (this text classifies
    # as 'price', so the composer never consults match_faq for it). See
    # test_conversation_reply_composer for that gate.
    assert match_faq("早餐多少錢嗎").topic == "breakfast"


def test_normalized_full_width_input_matches() -> None:
    # Full-width latin "ｗｉｆｉ" folds to "wifi" under NFKC (the same normalize
    # the parsers use); the matcher then hits it.
    assert match_faq(normalize_for_parsing("有ｗｉｆｉ嗎")).topic == "wifi"
