import inspect
from datetime import datetime, timezone

import pytest

from app.domain.inquiry_decision import InquiryDecision
from app.domain.inquiry_parser import parse_inquiry
from app.domain.reply_text import (
    MISSING_INFO_HEADER,
    OWNER_PUSH_UNCATEGORIZED_PREFIX,
    OWNER_PUSH_URGENT_PREFIX,
    SINGLE_MISSING_GUEST_COUNT_MESSAGE,
)
from app.schemas import InboundMessage
from app.services.inquiry_service import InquiryService


class FakeOperationModeService:
    def __init__(self, *, return_value: bool) -> None:
        self._return_value = return_value
        self.calls: list[tuple] = []

    def is_system_active(self, *, tenant_id: int, tenant_timezone: str) -> bool:
        self.calls.append((tenant_id, tenant_timezone))
        return self._return_value


def _build_message(
    text: str,
    *,
    tenant_id: int = 1,
    tenant_slug: str = "test-villa",
    tenant_timezone: str = "Asia/Taipei",
    platform: str = "line",
    platform_user_id: str = "user-123",
    customer_display_name: str | None = "Test User",
    timestamp: datetime | None = None,
) -> InboundMessage:
    return InboundMessage(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        tenant_timezone=tenant_timezone,
        platform=platform,
        platform_user_id=platform_user_id,
        customer_display_name=customer_display_name,
        text=text,
        timestamp=timestamp or datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
    )


def _build_service(*, system_on: bool = True) -> tuple[InquiryService, FakeOperationModeService]:
    fake = FakeOperationModeService(return_value=system_on)
    return InquiryService(operation_mode_service=fake), fake


# ============================================================
# URGENT BRANCH
# ============================================================


def test_urgent_in_on_mode_returns_push_owner_urgent() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(message=_build_message("瓦斯漏氣!"))

    assert decision.action_type == "push_owner_urgent"
    assert decision.was_urgent is True
    assert decision.customer_reply_text is None


def test_urgent_in_off_mode_still_pushes_owner_urgent() -> None:
    service, _ = _build_service(system_on=False)

    decision = service.handle_message(message=_build_message("火災!"))

    assert decision.action_type == "push_owner_urgent"
    assert decision.was_urgent is True


def test_urgent_log_payload_contains_category_and_keywords() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(message=_build_message("瓦斯漏氣"))

    assert decision.log_payload["urgency_category"] == "gas"
    assert "瓦斯漏" in decision.log_payload["urgency_matched_keywords"]


def test_urgent_owner_push_text_uses_template() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(message=_build_message("火災!"))

    assert decision.owner_push_text is not None
    assert OWNER_PUSH_URGENT_PREFIX in decision.owner_push_text
    assert "火災" in decision.owner_push_text


def test_urgent_does_not_call_is_system_active() -> None:
    service, fake = _build_service(system_on=True)

    service.handle_message(message=_build_message("火災!"))

    assert fake.calls == []


# ============================================================
# OFF-MODE BRANCH
# ============================================================


def test_off_mode_non_urgent_returns_do_nothing() -> None:
    service, _ = _build_service(system_on=False)

    decision = service.handle_message(message=_build_message("你好"))

    assert decision.action_type == "do_nothing"
    assert decision.was_system_off is True
    assert decision.customer_reply_text is None
    assert decision.owner_push_text is None


def test_off_mode_log_payload_has_off_state_and_action() -> None:
    service, _ = _build_service(system_on=False)

    decision = service.handle_message(message=_build_message("你好"))

    assert decision.log_payload["system_state_at_time"] == "off"
    assert decision.log_payload["action_taken"] == "off_mode_logged_only"


def test_off_mode_parser_runs_and_parsed_fields_logged() -> None:
    service, _ = _build_service(system_on=False)

    decision = service.handle_message(
        message=_build_message("6/15 入住 6/17 退房 4 大人 多少錢?")
    )

    assert decision.log_payload["parsed_checkin"] is not None
    assert decision.log_payload["parsed_checkout"] is not None
    assert decision.log_payload["parsed_adult_count"] == 4
    assert decision.log_payload["inquiry_intent"] == "price"


# ============================================================
# NON-INQUIRY BRANCH
# ============================================================


def test_non_inquiry_on_mode_pushes_owner() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(message=_build_message("今天天氣不錯"))

    assert decision.action_type == "push_to_owner_only"
    assert decision.owner_push_text is not None
    assert OWNER_PUSH_UNCATEGORIZED_PREFIX in decision.owner_push_text


def test_non_inquiry_log_payload_records_intent() -> None:
    text = "今天天氣不錯"
    expected_intent = parse_inquiry(text).intent.inquiry_type

    service, _ = _build_service(system_on=True)
    decision = service.handle_message(message=_build_message(text))

    assert decision.log_payload["inquiry_intent"] == expected_intent


def test_non_inquiry_customer_reply_is_none() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(message=_build_message("今天天氣不錯"))

    assert decision.customer_reply_text is None


# ============================================================
# MISSING-INFO BRANCH
# ============================================================


def test_missing_info_single_field_uses_single_template() -> None:
    # Full dates + intent, only guest count missing → single-template.
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/14 退房 多少錢?")
    )

    assert decision.action_type == "reply_to_customer_only"
    assert decision.customer_reply_text == SINGLE_MISSING_GUEST_COUNT_MESSAGE


def test_missing_info_multi_field_uses_multi_template() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(message=_build_message("多少錢?"))

    assert decision.action_type == "reply_to_customer_only"
    assert MISSING_INFO_HEADER in decision.customer_reply_text


def test_missing_info_action_taken_recorded() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(message=_build_message("多少錢?"))

    assert decision.log_payload["action_taken"] == "missing_info"


def test_missing_info_log_payload_missing_fields_populated() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(message=_build_message("多少錢?"))

    assert decision.log_payload["missing_fields"]
    assert isinstance(decision.log_payload["missing_fields"], list)


# ============================================================
# ROUTING / DISPATCH
# ============================================================


@pytest.mark.parametrize(
    "system_on,text,expected_action",
    [
        (True, "火災!", "push_owner_urgent"),
        (False, "你好", "do_nothing"),
        (True, "今天天氣不錯", "push_to_owner_only"),
        (True, "多少錢?", "reply_to_customer_only"),
    ],
)
def test_handle_message_returns_correct_action_type_per_branch(
    system_on: bool, text: str, expected_action: str
) -> None:
    service, _ = _build_service(system_on=system_on)

    decision = service.handle_message(message=_build_message(text))

    assert decision.action_type == expected_action


@pytest.mark.parametrize(
    "text",
    ["", " ", "   ", "??", "hello", "今天天氣不錯", "x" * 5000],
)
def test_handle_message_never_raises(text: str) -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(message=_build_message(text))

    assert isinstance(decision, InquiryDecision)


def test_handle_pricing_stub_raises_not_implemented() -> None:
    service, _ = _build_service(system_on=True)
    message = _build_message("6/15 入住 6/17 退房 4 大人 多少錢?")
    inquiry = parse_inquiry(message.text)

    with pytest.raises(NotImplementedError):
        service._handle_pricing(message, inquiry)


# ============================================================
# DISCIPLINE
# ============================================================


def test_no_method_exceeds_line_budget() -> None:
    """Public methods ≤15 body lines; private helpers ≤25."""
    for name, method in inspect.getmembers(
        InquiryService, predicate=inspect.isfunction
    ):
        if name == "__init__":
            continue
        source = inspect.getsource(method)
        lines = [
            line
            for line in source.split("\n")
            if line.strip() and not line.strip().startswith('"""')
        ]
        body_lines = len(lines) - 1
        limit = 25 if name.startswith("_") else 15
        kind = "private" if name.startswith("_") else "public"
        assert body_lines <= limit, (
            f"{kind} method {name} has {body_lines} body lines, max is {limit}"
        )
