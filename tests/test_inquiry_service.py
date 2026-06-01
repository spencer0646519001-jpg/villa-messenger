import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.domain.inquiry_decision import InquiryDecision
from app.domain.inquiry_parser import parse_inquiry
from app.domain.reply_text import (
    INVALID_DATE_MESSAGE,
    MISSING_INFO_HEADER,
    OVER_CAPACITY_MESSAGE,
    OWNER_PUSH_UNCATEGORIZED_PREFIX,
    OWNER_PUSH_URGENT_PREFIX,
    QUOTE_GREETING,
    SINGLE_MISSING_GUEST_COUNT_MESSAGE,
)
from app.schemas import InboundMessage
from app.services.inquiry_service import InquiryService


_DEFAULT_PRICING = {
    "base_prices_per_night": {
        "8_people": {
            "weekday": 9000,
            "saturday": 15000,
            "summer_weekday": 12000,
            "summer_saturday_or_holiday": 15000,
            "spring_festival": 25000,
        },
        "10_people": {
            "weekday": 12000,
            "saturday": 18000,
            "summer_weekday": 15000,
            "summer_saturday_or_holiday": 18000,
            "spring_festival": 28000,
        },
        "12_people": {
            "weekday": 15000,
            "saturday": 21000,
            "summer_weekday": 18000,
            "summer_saturday_or_holiday": 21000,
            "spring_festival": 31000,
        },
    },
}


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


def _build_service(
    *,
    system_on: bool = True,
    tenant_pricing: dict | None = None,
    tenant_special_dates: dict | None = None,
    availability_service=None,
) -> tuple[InquiryService, FakeOperationModeService]:
    fake = FakeOperationModeService(return_value=system_on)
    pricing = tenant_pricing if tenant_pricing is not None else _DEFAULT_PRICING
    special = tenant_special_dates if tenant_special_dates is not None else {}
    service = InquiryService(
        operation_mode_service=fake,
        tenant_pricing_loader=lambda tid: pricing,
        tenant_special_dates_loader=lambda tid: special,
        availability_service=availability_service,
    )
    return service, fake


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


def test_non_inquiry_owner_push_never_leaks_raw_userid() -> None:
    # KEY regression guard: with no display name (production reality -- the
    # adapter does not fetch profiles), the owner push must use the friendly
    # no-name format and NEVER print the raw U... userId.
    service, _ = _build_service(system_on=True)
    user_id = "Udd1dffaa94e003982bcb9e011655d4de"

    decision = service.handle_message(
        message=_build_message(
            "今天天氣不錯", platform_user_id=user_id, customer_display_name=None
        )
    )

    assert user_id not in decision.owner_push_text
    assert "客人:" not in decision.owner_push_text  # no name -> no customer line
    assert "客人問:今天天氣不錯" in decision.owner_push_text
    # Truthfulness: non-inquiry sends NO customer reply, so the push must not
    # claim one -- it uses the non-asserting hand-off close.
    assert "系統已回覆" not in decision.owner_push_text
    assert "尚未回覆客人,請您接手" in decision.owner_push_text


def test_urgent_owner_push_never_leaks_raw_userid() -> None:
    # Same regression guard for the urgent path.
    service, _ = _build_service(system_on=True)
    user_id = "Udd1dffaa94e003982bcb9e011655d4de"

    decision = service.handle_message(
        message=_build_message(
            "火災!", platform_user_id=user_id, customer_display_name=None
        )
    )

    assert user_id not in decision.owner_push_text
    assert "【緊急】" in decision.owner_push_text  # urgency marker preserved


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


# ============================================================
# PRICING HAPPY PATH
# ============================================================


def test_pricing_complete_weekday_inquiry_returns_quote() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?")
    )

    assert decision.action_type == "reply_to_customer_only"
    assert decision.could_quote is True
    assert decision.customer_reply_text is not None
    assert QUOTE_GREETING in decision.customer_reply_text


def test_pricing_log_payload_quoted_total_matches_pricing() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?")
    )

    # 4 adults, 1 weekday night, 8_people tier weekday=9000, no discount
    assert decision.log_payload["quoted_total"] == 9000


def test_pricing_action_taken_is_quoted() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?")
    )

    assert decision.log_payload["action_taken"] == "quoted"


def test_pricing_log_payload_includes_parsed_fields() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?")
    )

    assert decision.log_payload["parsed_checkin"] == "2026-05-12"
    assert decision.log_payload["parsed_checkout"] == "2026-05-13"
    assert decision.log_payload["parsed_adult_count"] == 4
    assert decision.log_payload["inquiry_intent"] == "price"


# ============================================================
# OVER-CAPACITY
# ============================================================


def test_over_capacity_reply_uses_over_capacity_template() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 17 大人 多少錢?")
    )

    assert decision.action_type == "reply_to_customer_only"
    assert decision.customer_reply_text == OVER_CAPACITY_MESSAGE
    assert decision.log_payload["action_taken"] == "over_capacity"


def test_over_capacity_could_quote_false_and_no_quoted_total() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 17 大人 多少錢?")
    )

    assert decision.could_quote is False
    assert decision.log_payload["quoted_total"] is None


# ============================================================
# INVALID DATE
# ============================================================


def test_invalid_date_reply_uses_invalid_date_template() -> None:
    # checkout (5/12) earlier than checkin (5/14)
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message("5/14 入住 5/12 退房 4 大人 多少錢?")
    )

    assert decision.action_type == "reply_to_customer_only"
    assert decision.customer_reply_text == INVALID_DATE_MESSAGE


def test_invalid_date_action_taken() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message("5/14 入住 5/12 退房 4 大人 多少錢?")
    )

    assert decision.log_payload["action_taken"] == "invalid_date"


# ============================================================
# LOADER INJECTION
# ============================================================


def test_tenant_pricing_loader_called_with_correct_tenant_id() -> None:
    pricing_calls: list[int] = []

    def pricing_loader(tid: int) -> dict:
        pricing_calls.append(tid)
        return _DEFAULT_PRICING

    fake = FakeOperationModeService(return_value=True)
    service = InquiryService(
        operation_mode_service=fake,
        tenant_pricing_loader=pricing_loader,
        tenant_special_dates_loader=lambda tid: {},
    )

    service.handle_message(
        message=_build_message(
            "5/12 入住 5/13 退房 4 大人 多少錢?", tenant_id=42
        )
    )

    assert pricing_calls == [42]


def test_special_dates_loader_called_and_fakes_work_end_to_end() -> None:
    special_calls: list[int] = []

    def special_loader(tid: int) -> dict:
        special_calls.append(tid)
        return {}

    fake = FakeOperationModeService(return_value=True)
    service = InquiryService(
        operation_mode_service=fake,
        tenant_pricing_loader=lambda tid: _DEFAULT_PRICING,
        tenant_special_dates_loader=special_loader,
    )

    decision = service.handle_message(
        message=_build_message(
            "5/12 入住 5/13 退房 4 大人 多少錢?", tenant_id=7
        )
    )

    assert special_calls == [7]
    assert decision.could_quote is True


# ============================================================
# INTEGRATION SPOT-CHECK (real fixture)
# ============================================================


def test_integration_spring_festival_quote_uses_real_fixture() -> None:
    config_path = (
        Path(__file__).resolve().parent.parent
        / "data"
        / "tenants"
        / "zhen123-house"
        / "config.json"
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))

    fake = FakeOperationModeService(return_value=True)
    service = InquiryService(
        operation_mode_service=fake,
        tenant_pricing_loader=lambda tid: config["pricing"],
        tenant_special_dates_loader=lambda tid: config["special_dates"],
    )

    decision = service.handle_message(
        message=_build_message("2/15 入住 2/17 退房 4 大人 多少錢?")
    )

    assert decision.action_type == "reply_to_customer_only"
    assert decision.could_quote is True
    assert "春節房價" in decision.customer_reply_text


# ============================================================
# IS_NIGHT IN LOG_PAYLOAD
# ============================================================


def test_log_payload_includes_is_night_key() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(message=_build_message("你好"))

    assert "is_night" in decision.log_payload


def test_is_night_false_for_daytime_local_message() -> None:
    # 06:00 UTC == 14:00 Asia/Taipei → clearly daytime.
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message(
            "你好",
            timestamp=datetime(2026, 5, 13, 6, 0, tzinfo=timezone.utc),
        )
    )

    assert decision.log_payload["is_night"] is False


def test_is_night_true_for_late_night_local_message() -> None:
    # 15:30 UTC == 23:30 Asia/Taipei → night (hour >= 23).
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message(
            "你好",
            timestamp=datetime(2026, 5, 13, 15, 30, tzinfo=timezone.utc),
        )
    )

    assert decision.log_payload["is_night"] is True


def test_is_night_true_for_early_morning_local_message() -> None:
    # 23:30 UTC on May 12 == 07:30 Asia/Taipei on May 13 → night (hour < 8).
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message(
            "你好",
            timestamp=datetime(2026, 5, 12, 23, 30, tzinfo=timezone.utc),
        )
    )

    assert decision.log_payload["is_night"] is True


def test_is_night_false_at_22_30_local_boundary() -> None:
    # 14:30 UTC == 22:30 Asia/Taipei. Under 23:00-08:00 window, this is daytime.
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message(
            "你好",
            timestamp=datetime(2026, 5, 13, 14, 30, tzinfo=timezone.utc),
        )
    )

    assert decision.log_payload["is_night"] is False


def test_is_night_present_on_urgent_path() -> None:
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(message=_build_message("火災!"))

    assert "is_night" in decision.log_payload
    assert isinstance(decision.log_payload["is_night"], bool)


def test_is_night_uses_tenant_timezone_not_utc() -> None:
    # 15:00 UTC == 23:00 Asia/Taipei (night), but 23:00 UTC == 07:00 Asia/Taipei
    # (still night under 23:00-08:00, so we use a UTC instant that is daytime
    # in Taipei to prove the conversion happens).
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message(
            "你好",
            tenant_timezone="Asia/Taipei",
            timestamp=datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc),
        )
    )

    # 15:00 UTC is daytime in UTC but 23:00 (night) in Asia/Taipei.
    assert decision.log_payload["is_night"] is True


# ============================================================
# AVAILABILITY-GATED PRICING
# ============================================================


from datetime import date as _date  # local alias so the new section is self-contained

from app.domain.availability_models import AvailabilityResult, BlockedNight
from app.domain.reply_text import (
    FULL_HOUSE_MESSAGE,
    OWNER_PUSH_AVAILABILITY_UNVERIFIED_PREFIX,
    OWNER_PUSH_FULL_HOUSE_PREFIX,
)
from app.services.availability_service import AvailabilityCheckOutcome


class _FakeAvailabilityService:
    """Stand-in for AvailabilityService. Records calls; returns a canned
    outcome. Lets tests exercise the three pricing-gate branches without
    touching GoogleCalendarClient or the network."""

    def __init__(self, *, outcome: AvailabilityCheckOutcome) -> None:
        self._outcome = outcome
        self.calls: list[tuple[_date, _date]] = []

    def check(self, *, checkin_date, checkout_date) -> AvailabilityCheckOutcome:
        self.calls.append((checkin_date, checkout_date))
        return self._outcome


def _outcome_blocked(*, nights: list[_date]) -> AvailabilityCheckOutcome:
    return AvailabilityCheckOutcome(
        status="blocked",
        result=AvailabilityResult(
            has_any_blocked_nights=True,
            blocked_nights=[
                BlockedNight(
                    night_date=n, blocking_event_summary="枕123", matched_keyword="枕"
                )
                for n in nights
            ],
        ),
    )


def _outcome_available() -> AvailabilityCheckOutcome:
    return AvailabilityCheckOutcome(
        status="available",
        result=AvailabilityResult(has_any_blocked_nights=False, blocked_nights=[]),
    )


def _outcome_error(reason: str) -> AvailabilityCheckOutcome:
    return AvailabilityCheckOutcome(status="error", error_reason=reason)


# ---------- Default behavior preserved: no availability_service injected ----------


def test_pricing_without_availability_service_quotes_as_before() -> None:
    # This is the existing-tests invariant: when no availability_service is
    # passed, behavior is identical to PR8.
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?")
    )

    assert decision.action_type == "reply_to_customer_only"
    assert decision.could_quote is True
    assert decision.log_payload["action_taken"] == "quoted"


# ---------- BLOCKED branch ----------


def test_blocked_returns_reply_and_push_full_house_decision() -> None:
    fake_avail = _FakeAvailabilityService(
        outcome=_outcome_blocked(nights=[_date(2026, 5, 12)])
    )
    service, _ = _build_service(system_on=True, availability_service=fake_avail)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?")
    )

    assert decision.action_type == "reply_and_push"
    assert decision.customer_reply_text == FULL_HOUSE_MESSAGE
    assert decision.owner_push_text is not None
    assert OWNER_PUSH_FULL_HOUSE_PREFIX in decision.owner_push_text
    assert decision.could_quote is False  # default — no quote produced
    assert decision.parsed_as_inquiry is True


def test_blocked_log_payload_action_taken_and_blocked_count() -> None:
    fake_avail = _FakeAvailabilityService(
        outcome=_outcome_blocked(nights=[_date(2026, 5, 12), _date(2026, 5, 13)])
    )
    service, _ = _build_service(system_on=True, availability_service=fake_avail)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/14 退房 4 大人 多少錢?")
    )

    assert decision.log_payload["action_taken"] == "full_house"
    assert decision.log_payload["blocked_nights_count"] == 2
    assert decision.log_payload["parsed_checkin"] == "2026-05-12"
    assert decision.log_payload["parsed_checkout"] == "2026-05-14"
    assert decision.log_payload["quoted_total"] is None  # blocked path does not quote


def test_blocked_owner_push_text_omits_inquiry_id_at_service_layer() -> None:
    # The persistence layer (PR8) assigns inquiry_id; the service has none
    # yet. The renderer was extended to omit the line when inquiry_id=None.
    fake_avail = _FakeAvailabilityService(
        outcome=_outcome_blocked(nights=[_date(2026, 5, 12)])
    )
    service, _ = _build_service(system_on=True, availability_service=fake_avail)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?")
    )

    assert "詢價編號" not in decision.owner_push_text


def test_blocked_availability_service_called_with_parsed_dates() -> None:
    fake_avail = _FakeAvailabilityService(
        outcome=_outcome_blocked(nights=[_date(2026, 5, 12)])
    )
    service, _ = _build_service(system_on=True, availability_service=fake_avail)

    service.handle_message(message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?"))

    assert fake_avail.calls == [(_date(2026, 5, 12), _date(2026, 5, 13))]


# ---------- AVAILABLE branch ----------


def test_available_outcome_proceeds_to_normal_quote() -> None:
    fake_avail = _FakeAvailabilityService(outcome=_outcome_available())
    service, _ = _build_service(system_on=True, availability_service=fake_avail)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?")
    )

    assert decision.action_type == "reply_to_customer_only"
    assert decision.could_quote is True
    assert decision.log_payload["action_taken"] == "quoted"
    assert decision.log_payload["quoted_total"] == 9000


# ---------- ERROR (fallback) branch ----------


def test_error_outcome_falls_back_to_quote_plus_owner_notice() -> None:
    fake_avail = _FakeAvailabilityService(
        outcome=_outcome_error("calendar fetch failed: HTTP 500")
    )
    service, _ = _build_service(system_on=True, availability_service=fake_avail)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?")
    )

    assert decision.action_type == "reply_and_push"
    assert decision.could_quote is True
    # Customer still sees the normal quote — graceful degradation.
    assert decision.customer_reply_text is not None
    assert "費用明細" in decision.customer_reply_text
    # Owner gets the unverified-availability notice.
    assert decision.owner_push_text is not None
    assert OWNER_PUSH_AVAILABILITY_UNVERIFIED_PREFIX in decision.owner_push_text


def test_error_outcome_log_payload_carries_action_and_reason() -> None:
    fake_avail = _FakeAvailabilityService(
        outcome=_outcome_error("calendar fetch failed: network down")
    )
    service, _ = _build_service(system_on=True, availability_service=fake_avail)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?")
    )

    assert decision.log_payload["action_taken"] == "quoted_unverified"
    assert "network down" in decision.log_payload["availability_error_reason"]
    assert decision.log_payload["quoted_total"] == 9000


# ---------- INTERACTION WITH OTHER GATES ----------


def test_invalid_date_short_circuits_before_availability_check() -> None:
    # Invalid date is a pricing-layer failure that takes precedence: we
    # should not even attempt a calendar lookup for nonsensical date ranges.
    fake_avail = _FakeAvailabilityService(outcome=_outcome_available())
    service, _ = _build_service(system_on=True, availability_service=fake_avail)

    decision = service.handle_message(
        message=_build_message("5/14 入住 5/12 退房 4 大人 多少錢?")
    )

    assert decision.log_payload["action_taken"] == "invalid_date"
    assert fake_avail.calls == []  # availability service never called


def test_over_capacity_short_circuits_before_availability_check() -> None:
    fake_avail = _FakeAvailabilityService(outcome=_outcome_available())
    service, _ = _build_service(system_on=True, availability_service=fake_avail)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 17 大人 多少錢?")
    )

    assert decision.log_payload["action_taken"] == "over_capacity"
    assert fake_avail.calls == []


def test_missing_info_does_not_call_availability_service() -> None:
    fake_avail = _FakeAvailabilityService(outcome=_outcome_available())
    service, _ = _build_service(system_on=True, availability_service=fake_avail)

    service.handle_message(message=_build_message("5/12 入住 5/14 退房 多少錢?"))

    assert fake_avail.calls == []


def test_urgent_does_not_call_availability_service() -> None:
    fake_avail = _FakeAvailabilityService(outcome=_outcome_available())
    service, _ = _build_service(system_on=True, availability_service=fake_avail)

    service.handle_message(message=_build_message("瓦斯漏氣! 5/12 入住 5/13 退房 4 大人"))

    assert fake_avail.calls == []


# ---------- FULL-WIDTH IME INPUT (parse-normalized / store-original) ----------


def test_full_width_input_parses_dates_but_logs_original_raw_text() -> None:
    # End-to-end proof of the split: a full-width customer message must parse
    # (parsed_checkin populated) while log_payload["raw_text"] keeps the exact
    # original full-width text for the stored record / raw_log_payload.
    service, _ = _build_service(system_on=True)

    decision = service.handle_message(
        message=_build_message("６／１４有房嗎")
    )

    assert decision.log_payload["raw_text"] == "６／１４有房嗎"
    assert decision.log_payload["parsed_checkin"] == "2026-06-14"


def test_off_mode_does_not_call_availability_service() -> None:
    fake_avail = _FakeAvailabilityService(
        outcome=_outcome_blocked(nights=[_date(2026, 5, 12)])
    )
    service, _ = _build_service(system_on=False, availability_service=fake_avail)

    decision = service.handle_message(
        message=_build_message("5/12 入住 5/13 退房 4 大人 多少錢?")
    )

    assert decision.action_type == "do_nothing"
    assert fake_avail.calls == []


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
