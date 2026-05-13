import pytest
from pydantic import ValidationError

from app.domain.inquiry_decision import InquiryDecision


_LOG = {"tenant_id": 1, "action_taken": "test"}


def test_reply_to_customer_only_valid() -> None:
    decision = InquiryDecision(
        action_type="reply_to_customer_only",
        customer_reply_text="hello",
        log_payload=_LOG,
    )

    assert decision.action_type == "reply_to_customer_only"
    assert decision.customer_reply_text == "hello"
    assert decision.owner_push_text is None


def test_reply_to_customer_only_requires_reply_text() -> None:
    with pytest.raises(ValidationError):
        InquiryDecision(action_type="reply_to_customer_only", log_payload=_LOG)


def test_reply_to_customer_only_rejects_owner_push_text() -> None:
    with pytest.raises(ValidationError):
        InquiryDecision(
            action_type="reply_to_customer_only",
            customer_reply_text="hi",
            owner_push_text="oops",
            log_payload=_LOG,
        )


def test_push_to_owner_only_valid() -> None:
    decision = InquiryDecision(
        action_type="push_to_owner_only",
        owner_push_text="non-inquiry",
        log_payload=_LOG,
    )

    assert decision.owner_push_text == "non-inquiry"
    assert decision.customer_reply_text is None


def test_reply_and_push_valid() -> None:
    decision = InquiryDecision(
        action_type="reply_and_push",
        customer_reply_text="full house",
        owner_push_text="please confirm",
        log_payload=_LOG,
    )

    assert decision.customer_reply_text == "full house"
    assert decision.owner_push_text == "please confirm"


def test_reply_and_push_requires_both_texts() -> None:
    with pytest.raises(ValidationError):
        InquiryDecision(
            action_type="reply_and_push",
            customer_reply_text="only reply",
            log_payload=_LOG,
        )


def test_do_nothing_valid_with_off_mode_flag() -> None:
    decision = InquiryDecision(
        action_type="do_nothing",
        was_system_off=True,
        log_payload=_LOG,
    )

    assert decision.customer_reply_text is None
    assert decision.owner_push_text is None
    assert decision.was_system_off is True


def test_do_nothing_rejects_reply_text() -> None:
    with pytest.raises(ValidationError):
        InquiryDecision(
            action_type="do_nothing",
            customer_reply_text="should not be here",
            log_payload=_LOG,
        )


def test_push_owner_urgent_valid() -> None:
    decision = InquiryDecision(
        action_type="push_owner_urgent",
        owner_push_text="URGENT: 火災",
        was_urgent=True,
        log_payload=_LOG,
    )

    assert decision.was_urgent is True
    assert decision.owner_push_text == "URGENT: 火災"
    assert decision.customer_reply_text is None


def test_push_owner_urgent_requires_was_urgent_true() -> None:
    with pytest.raises(ValidationError):
        InquiryDecision(
            action_type="push_owner_urgent",
            owner_push_text="urgent text",
            was_urgent=False,
            log_payload=_LOG,
        )


def test_was_urgent_true_rejects_non_urgent_action_type() -> None:
    with pytest.raises(ValidationError):
        InquiryDecision(
            action_type="push_to_owner_only",
            owner_push_text="not urgent",
            was_urgent=True,
            log_payload=_LOG,
        )


def test_was_system_off_rejects_was_urgent_true() -> None:
    with pytest.raises(ValidationError):
        InquiryDecision(
            action_type="push_owner_urgent",
            owner_push_text="urgent",
            was_urgent=True,
            was_system_off=True,
            log_payload=_LOG,
        )


def test_empty_log_payload_rejected() -> None:
    with pytest.raises(ValidationError):
        InquiryDecision(
            action_type="reply_to_customer_only",
            customer_reply_text="hi",
            log_payload={},
        )


def test_invalid_action_type_literal_rejected() -> None:
    with pytest.raises(ValidationError):
        InquiryDecision(
            action_type="not_a_real_action",  # type: ignore[arg-type]
            log_payload=_LOG,
        )
