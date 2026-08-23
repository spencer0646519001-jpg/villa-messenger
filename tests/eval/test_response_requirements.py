from app.domain.reply_text import SAFETY_NOTE
from app.services.conversation_reply_composer import ComposedReply
from eval.response_requirements import (
    NOT_DETERMINISTIC,
    check_must_include,
    check_must_not_claim,
    parse_tag,
)


def test_parse_tag_splits_name_and_args():
    parsed = parse_tag("disclose_assumed_one_night_range(8/10-8/11)")
    assert parsed.name == "disclose_assumed_one_night_range"
    assert parsed.args == "8/10-8/11"

    literal = parse_tag("ask_room_count")
    assert literal.name == "ask_room_count"
    assert literal.args is None


def test_must_include_quote_scope_disclaimer():
    composed = ComposedReply(text=f"...\n{SAFETY_NOTE}")
    assert check_must_include("quote_scope_disclaimer", composed) is True

    composed_missing = ComposedReply(text="沒有揭露句子")
    assert check_must_include("quote_scope_disclaimer", composed_missing) is False


def test_must_include_parametrized_disclose_assumed_one_night_range():
    composed = ComposedReply(text="入住 8/10、退房 8/11(住一晚)目前可能已有訂房")
    assert check_must_include("disclose_assumed_one_night_range(8/10-8/11)", composed) is True

    composed_wrong_dates = ComposedReply(text="入住 8/15、退房 8/16(住一晚)目前可能已有訂房")
    assert check_must_include("disclose_assumed_one_night_range(8/10-8/11)", composed_wrong_dates) is False


def test_must_include_unregistered_tag_is_not_deterministic():
    composed = ComposedReply(text="anything")
    assert check_must_include("some_never_registered_tag", composed) == NOT_DETERMINISTIC


def test_must_not_claim_passes_when_banned_phrase_absent():
    composed = ComposedReply(text="此為系統依目前規則初步估算,實際空房與最終價格仍會請民宿人員和您確認。")
    assert check_must_not_claim("guarantee_availability", composed) is True
    assert check_must_not_claim("confirm_booking_confirmed", composed) is True


def test_must_not_claim_fails_when_banned_phrase_present():
    composed = ComposedReply(text="您好,已確認訂房,期待您的光臨。")
    assert check_must_not_claim("confirm_booking_confirmed", composed) is False


def test_must_not_claim_unregistered_tag_is_not_deterministic():
    composed = ComposedReply(text="anything")
    assert check_must_not_claim("some_never_registered_tag", composed) == NOT_DETERMINISTIC
