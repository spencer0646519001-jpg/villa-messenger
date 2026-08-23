from app.domain.inquiry_decision import InquiryDecision
from app.domain.reply_text import (
    MISSING_ROOM_COUNT_MESSAGE,
    QUOTE_GREETING,
    RECONFIRM_STALE_CONTEXT_MESSAGE,
)
from app.services.conversation_reply_composer import ComposedReply
from eval.action_taxonomy import ACTION_ALIASES, actions_match, classify_actual_action


def _decision(**overrides) -> InquiryDecision:
    base = dict(
        action_type="reply_to_customer_only",
        customer_reply_text="placeholder",
        log_payload={"action_taken": "missing_info"},
    )
    base.update(overrides)
    return InquiryDecision(**base)


def test_classifies_urgent_regardless_of_text():
    decision = InquiryDecision(
        action_type="push_owner_urgent",
        owner_push_text="urgent",
        was_urgent=True,
        log_payload={"action_taken": "urgent"},
    )
    composed = ComposedReply(owner_push_text="urgent")
    assert classify_actual_action(decision, composed, None) == "urgent_push_owner"


def test_classifies_off_mode_regardless_of_composed_text():
    decision = InquiryDecision(
        action_type="do_nothing",
        was_system_off=True,
        log_payload={"action_taken": "off_mode_logged_only"},
    )
    composed = ComposedReply(text=None)
    assert classify_actual_action(decision, composed, None) == "off_mode_logged_only"


def test_classifies_stale_context_reconfirmation():
    decision = _decision()
    composed = ComposedReply(text=RECONFIRM_STALE_CONTEXT_MESSAGE)
    assert classify_actual_action(decision, composed, {"id": 1}) == "stale_context_reconfirmation"


def test_classifies_missing_room_count():
    decision = _decision(log_payload={"action_taken": "missing_room_count"})
    composed = ComposedReply(text=MISSING_ROOM_COUNT_MESSAGE)
    assert classify_actual_action(decision, composed, None) == "missing_room_count"


def test_classifies_quoted_vs_quoted_unverified():
    decision = _decision(log_payload={"action_taken": "quoted"})
    quoted = ComposedReply(text=f"{QUOTE_GREETING}\n...")
    unverified = ComposedReply(text=f"{QUOTE_GREETING}\n...", owner_push_text="📩 空房未確認")

    assert classify_actual_action(decision, quoted, {"id": 1}) == "quoted"
    assert classify_actual_action(decision, unverified, {"id": 1}) == "quoted_unverified"


def test_unrecognized_reply_text_is_unknown_not_silently_passed():
    decision = _decision()
    composed = ComposedReply(text="something the classifier has never seen before")
    assert classify_actual_action(decision, composed, None) == "unknown"


def test_alias_lets_actual_stale_reconfirm_satisfy_gold_compound_label():
    compound_label = "stale_context_reconfirm_then_missing_room_count"
    assert compound_label in ACTION_ALIASES
    assert actions_match("stale_context_reconfirmation", compound_label) is True
    assert actions_match("missing_room_count", compound_label) is False


def test_actions_match_is_plain_equality_otherwise():
    assert actions_match("quoted", "quoted") is True
    assert actions_match("quoted", "missing_info") is False
