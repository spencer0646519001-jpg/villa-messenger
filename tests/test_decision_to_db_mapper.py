import dataclasses
import inspect
import json
from datetime import datetime, timezone

import pytest

from app.domain.decision_to_db_mapper import (
    DbWritePlan,
    build_db_write_plan,
)
import app.domain.decision_to_db_mapper as mapper_module
from app.domain.inquiry_decision import InquiryDecision
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


class _FakeOperationModeService:
    def __init__(self, *, return_value: bool) -> None:
        self._return_value = return_value

    def is_system_active(self, *, tenant_id: int, tenant_timezone: str) -> bool:
        return self._return_value


def _build_service(*, system_on: bool = True) -> InquiryService:
    return InquiryService(
        operation_mode_service=_FakeOperationModeService(return_value=system_on),
        tenant_pricing_loader=lambda tid: _DEFAULT_PRICING,
        tenant_special_dates_loader=lambda tid: {},
    )


def _build_message(text: str) -> InboundMessage:
    return InboundMessage(
        tenant_id=1,
        tenant_slug="test-villa",
        tenant_timezone="Asia/Taipei",
        platform="line",
        platform_user_id="user-123",
        customer_display_name="Test User",
        text=text,
        timestamp=datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
    )


def _decision_for(text: str, *, system_on: bool = True) -> InquiryDecision:
    return _build_service(system_on=system_on).handle_message(
        message=_build_message(text)
    )


# ============================================================
# CORE BRANCHES
# ============================================================


def test_happy_quote_produces_both_rows_with_quoted_total() -> None:
    decision = _decision_for("5/12 入住 5/13 退房 4 大人 多少錢?")

    plan = build_db_write_plan(decision)

    assert plan.messages_row["category"] == "quoted"
    assert plan.inquiry_row is not None
    assert plan.inquiry_row["estimated_total_price"] == 9000
    assert plan.inquiry_row["adult_count"] == 4


def test_over_capacity_inquiry_row_present_without_quoted_total() -> None:
    decision = _decision_for("5/12 入住 5/13 退房 17 大人 多少錢?")

    plan = build_db_write_plan(decision)

    assert plan.messages_row["category"] == "over_capacity"
    assert plan.inquiry_row is not None
    assert plan.inquiry_row["estimated_total_price"] is None


def test_invalid_date_produces_inquiry_row() -> None:
    decision = _decision_for("5/14 入住 5/12 退房 4 大人 多少錢?")

    plan = build_db_write_plan(decision)

    assert plan.messages_row["category"] == "invalid_date"
    assert plan.inquiry_row is not None
    assert plan.inquiry_row["estimated_total_price"] is None
    assert plan.inquiry_row["checkin_date"] == "2026-05-14"
    assert plan.inquiry_row["checkout_date"] == "2026-05-12"


def test_missing_info_produces_inquiry_row() -> None:
    decision = _decision_for("多少錢?")

    plan = build_db_write_plan(decision)

    assert plan.messages_row["category"] == "missing_info"
    assert plan.inquiry_row is not None
    assert plan.inquiry_row["inquiry_type"] == "price"


def test_non_inquiry_produces_only_messages_row() -> None:
    decision = _decision_for("今天天氣不錯")

    plan = build_db_write_plan(decision)

    assert plan.messages_row["category"] == "non_inquiry_uncategorized"
    assert plan.inquiry_row is None


def test_urgent_produces_only_messages_row_with_is_urgent_true() -> None:
    decision = _decision_for("火災!")

    plan = build_db_write_plan(decision)

    assert plan.messages_row["category"] == "urgent"
    assert plan.messages_row["is_urgent"] is True
    assert plan.inquiry_row is None


# ============================================================
# OFF MODE (Q3: off-mode parsed-as-inquiry still gets inquiry_row)
# ============================================================


def test_off_mode_parsed_as_inquiry_produces_inquiry_row() -> None:
    decision = _decision_for(
        "5/12 入住 5/13 退房 4 大人 多少錢?", system_on=False
    )

    plan = build_db_write_plan(decision)

    assert plan.messages_row["system_state_at_time"] == "off"
    assert plan.inquiry_row is not None
    assert plan.inquiry_row["adult_count"] == 4


def test_off_mode_non_inquiry_produces_only_messages_row() -> None:
    decision = _decision_for("你好", system_on=False)

    plan = build_db_write_plan(decision)

    assert plan.messages_row["system_state_at_time"] == "off"
    assert plan.inquiry_row is None


# ============================================================
# TENANT SAFETY
# ============================================================


def test_tenant_id_present_in_both_rows() -> None:
    decision = _decision_for("5/12 入住 5/13 退房 4 大人 多少錢?")

    plan = build_db_write_plan(decision)

    assert plan.messages_row["tenant_id"] == 1
    assert plan.inquiry_row is not None
    assert plan.inquiry_row["tenant_id"] == 1


# ============================================================
# FIELD NAMING (must match repository kwargs)
# ============================================================


def test_messages_row_keys_match_message_repository_kwargs() -> None:
    decision = _decision_for("5/12 入住 5/13 退房 4 大人 多少錢?")

    plan = build_db_write_plan(decision)

    expected_keys = {
        "tenant_id",
        "platform",
        "platform_user_id",
        "message_text",
        "category",
        "is_night",
        "system_state_at_time",
        "is_urgent",
        "raw_log_payload",
    }
    assert set(plan.messages_row.keys()) == expected_keys


def test_inquiry_row_keys_match_inquiry_repository_kwargs() -> None:
    decision = _decision_for("5/12 入住 5/13 退房 4 大人 多少錢?")

    plan = build_db_write_plan(decision)

    expected_keys = {
        "tenant_id",
        "platform",
        "platform_user_id",
        "inquiry_type",
        "original_message",
        "checkin_date",
        "checkout_date",
        "adult_count",
        "child_count",
        "infant_count",
        "pet_count",
        "estimated_total_price",
    }
    assert set(plan.inquiry_row.keys()) == expected_keys


# ============================================================
# IMMUTABILITY AND NONE PASSTHROUGH
# ============================================================


def test_db_write_plan_is_frozen() -> None:
    decision = _decision_for("你好")
    plan = build_db_write_plan(decision)

    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.messages_row = {}


def test_none_log_payload_values_pass_through_as_none() -> None:
    # Non-inquiry path leaves quoted_total, parsed_checkin, etc. as None
    # in log_payload. The mapper should preserve None (not convert to "None").
    decision = _decision_for("今天天氣不錯")

    plan = build_db_write_plan(decision)

    # Non-inquiry has inquiry_row=None, so the None passthrough check has to
    # live on a path that builds inquiry_row. Use a missing-info case where
    # pet_count, infant_count, child_count are None in log_payload.
    missing = _decision_for("5/12 入住 5/13 退房 4 大人 多少錢?")
    # 4 adults: child_count, infant_count, pet_count are None on price path.
    plan_missing = build_db_write_plan(missing)
    assert plan_missing.inquiry_row["child_count"] is None
    assert plan_missing.inquiry_row["infant_count"] is None
    assert plan_missing.inquiry_row["pet_count"] is None
    # And the non-inquiry messages_row preserves None passthrough indirectly
    # via category (which is "non_inquiry_uncategorized", not the string "None")
    assert plan.messages_row["category"] == "non_inquiry_uncategorized"


# ============================================================
# RAW LOG PAYLOAD (recovers received_at + urgency fields; PR8 debt)
# ============================================================


def test_raw_log_payload_round_trips_dropped_fields() -> None:
    # Urgent path exercises both fields PR8 dropped (urgency_category,
    # urgency_matched_keywords) plus received_at (lost when the repo
    # overwrites created_at with _utc_now_iso()).
    decision = _decision_for("火災!")

    plan = build_db_write_plan(decision)
    recovered = json.loads(plan.messages_row["raw_log_payload"])

    assert recovered["urgency_category"] == decision.log_payload["urgency_category"]
    assert (
        recovered["urgency_matched_keywords"]
        == decision.log_payload["urgency_matched_keywords"]
    )
    assert recovered["received_at"] == decision.log_payload["received_at"]


def test_raw_log_payload_is_valid_json_with_unescaped_chinese() -> None:
    decision = _decision_for("火災!")

    raw = build_db_write_plan(decision).messages_row["raw_log_payload"]

    # ensure_ascii=False keeps the original Chinese literal, not \uXXXX escapes.
    assert "火災" in raw
    assert json.loads(raw)["raw_text"] == "火災!"


# ============================================================
# DISCIPLINE
# ============================================================


def test_no_mapper_function_exceeds_line_budget() -> None:
    """Public function ≤15 body lines; private helpers ≤25."""
    for name, obj in inspect.getmembers(mapper_module, inspect.isfunction):
        if obj.__module__ != mapper_module.__name__:
            continue
        source = inspect.getsource(obj)
        lines = [
            line
            for line in source.split("\n")
            if line.strip() and not line.strip().startswith('"""')
        ]
        body_lines = len(lines) - 1
        limit = 25 if name.startswith("_") else 15
        kind = "private" if name.startswith("_") else "public"
        assert body_lines <= limit, (
            f"{kind} mapper function {name} has {body_lines} body lines, "
            f"max is {limit}"
        )
