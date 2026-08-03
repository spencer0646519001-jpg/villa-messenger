import json
from datetime import date
from pathlib import Path

import pytest

from app.domain.pricing_policy import calculate_price as _calculate_price
from app.domain.reply_templates import (
    render_assumed_single_night_full_house_message,
    _format_date_with_weekday,
    _format_guest_summary,
    _format_money,
    render_full_house_message,
    render_invalid_date_message,
    render_manual_review_message,
    render_missing_info_message,
    render_missing_room_count_message,
    render_over_capacity_message,
    render_owner_push_full_house,
    render_owner_push_uncategorized,
    render_owner_push_urgent,
    render_quote_message,
    render_room_capacity_suggestion_message,
)
from app.domain.reply_text import (
    BBQ_CONFIRMATION,
    CHILDREN_CONFIRMATION,
    FULL_HOUSE_MESSAGE,
    INFANTS_CONFIRMATION,
    INVALID_DATE_MESSAGE,
    MISSING_CHECKIN_LINE,
    MISSING_CHECKOUT_LINE,
    MISSING_GUEST_COUNT_LINE,
    MISSING_INFO_FOOTER,
    MISSING_INFO_HEADER,
    MISSING_PET_COUNT_LINE,
    OVER_CAPACITY_MESSAGE,
    OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_PREFIX,
    OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_UNKNOWN,
    OWNER_PUSH_FULL_HOUSE_PREFIX,
    OWNER_PUSH_UNCATEGORIZED_PREFIX,
    OWNER_PUSH_URGENT_PREFIX,
    PETS_CONFIRMATION,
    QUOTE_GREETING,
    SAFETY_NOTE,
    SINGLE_MISSING_CHECKIN_MESSAGE,
    SINGLE_MISSING_CHECKOUT_MESSAGE,
    SINGLE_MISSING_GUEST_COUNT_MESSAGE,
    SINGLE_MISSING_PET_COUNT_MESSAGE,
)


_ROOM_POLICY = {
    "standard_capacity": 12,
    "max_capacity": 16,
    "room_opening_rules": [
        {"max_people": 8, "rooms_opened": 2},
        {"max_people": 10, "rooms_opened": 3},
        {"max_people": 12, "rooms_opened": 4},
        {"min_people": 13, "max_people": 16, "rooms_opened": 4, "extra_beds": True},
    ],
}


def calculate_price(**kwargs):
    kwargs.setdefault("room_policy", _ROOM_POLICY)
    kwargs.setdefault("room_count", _legacy_equivalent_room_count(kwargs))
    return _calculate_price(**kwargs)


def _legacy_equivalent_room_count(kwargs: dict) -> int:
    guest_count = (kwargs.get("adult_count") or 0) + (kwargs.get("child_count") or 0)
    if guest_count <= 8:
        return 2
    if guest_count <= 10:
        return 3
    return 4


@pytest.fixture
def zhen123_pricing():
    config_path = Path("data/tenants/zhen123-house/config.json")
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)["pricing"]


@pytest.fixture
def zhen123_special_dates():
    config_path = Path("data/tenants/zhen123-house/config.json")
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)["special_dates"]


# --- Helpers ---


def test_format_date_with_weekday_tuesday() -> None:
    assert _format_date_with_weekday(date(2026, 5, 12)) == "2026/05/12(二)"


def test_format_date_with_weekday_spring_festival_chuyi() -> None:
    assert _format_date_with_weekday(date(2026, 2, 17)) == "2026/02/17(二)"


def test_format_money_thousands() -> None:
    assert _format_money(17500) == "NT$17,500"


def test_format_money_zero() -> None:
    assert _format_money(0) == "NT$0"


def test_format_money_nine_thousand() -> None:
    assert _format_money(9000) == "NT$9,000"


def test_format_guest_summary_only_adults() -> None:
    assert _format_guest_summary(4) == "4 大人(共 4 人)"


def test_format_guest_summary_adults_and_children() -> None:
    assert _format_guest_summary(2, 1) == "2 大 1 小(共 3 人)"


def test_format_guest_summary_with_infants() -> None:
    result = _format_guest_summary(2, 1, 1)
    assert "嬰" in result
    assert "共 3 人" in result


# --- render_quote_message ---


def test_quote_single_weekday_night_four_adults(zhen123_pricing) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )
    out = render_quote_message(
        pricing=pricing,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
    )

    assert QUOTE_GREETING in out
    assert "入住:2026/05/12(二)" in out
    assert "共 1 晚" in out
    assert "房型:開 2 間房" in out
    assert "小計:NT$9,000" in out
    assert SAFETY_NOTE in out
    assert CHILDREN_CONFIRMATION not in out
    assert PETS_CONFIRMATION not in out
    assert "連住折扣" not in out


def test_quote_two_weekday_nights_long_stay_discount(zhen123_pricing) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 5, 11),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )
    out = render_quote_message(
        pricing=pricing,
        checkin_date=date(2026, 5, 11),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
    )

    assert "共 2 晚" in out
    assert "連住折扣:-NT$1,000" in out
    assert "小計:NT$17,000" in out


def test_quote_with_children_confirmation(zhen123_pricing) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=2,
        child_count=2,
        tenant_pricing=zhen123_pricing,
    )
    out = render_quote_message(
        pricing=pricing,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=2,
        child_count=2,
    )

    assert "住宿人數:2 大 2 小(共 4 人)" in out
    assert CHILDREN_CONFIRMATION in out
    assert out.index(SAFETY_NOTE) < out.index(CHILDREN_CONFIRMATION)


def test_quote_with_infants_confirmation(zhen123_pricing) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        infant_count=1,
        tenant_pricing=zhen123_pricing,
    )
    out = render_quote_message(
        pricing=pricing,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        infant_count=1,
    )

    assert INFANTS_CONFIRMATION in out


def test_quote_with_pets(zhen123_pricing) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        pet_count=2,
        tenant_pricing=zhen123_pricing,
    )
    out = render_quote_message(
        pricing=pricing,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        pet_count=2,
    )

    assert "寵物:2 隻" in out
    assert "寵物清潔費:NT$1,000" in out
    assert PETS_CONFIRMATION in out


def test_quote_with_bbq(zhen123_pricing) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        wants_bbq=True,
        tenant_pricing=zhen123_pricing,
    )
    out = render_quote_message(
        pricing=pricing,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        wants_bbq=True,
    )

    assert "烤肉:是" in out
    assert "烤肉清潔費:NT$1,000" in out
    assert BBQ_CONFIRMATION in out


def test_quote_without_bbq_shows_no_bbq_lines(zhen123_pricing) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )
    out = render_quote_message(
        pricing=pricing,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
    )

    assert "烤肉" not in out
    assert BBQ_CONFIRMATION not in out


def test_quote_extra_person_fee(zhen123_pricing) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=13,
        tenant_pricing=zhen123_pricing,
    )
    out = render_quote_message(
        pricing=pricing,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=13,
    )

    assert "加人費:NT$1,000" in out
    assert "小計:NT$16,000" in out


def test_quote_spring_festival_full_9_nights(
    zhen123_pricing, zhen123_special_dates
) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 2, 14),
        checkout_date=date(2026, 2, 23),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates=zhen123_special_dates,
    )
    out = render_quote_message(
        pricing=pricing,
        checkin_date=date(2026, 2, 14),
        checkout_date=date(2026, 2, 23),
        adult_count=4,
    )

    assert out.count("春節房價") == 9
    assert "連住折扣:-NT$8,000" in out
    assert "小計:NT$217,000" in out


def test_quote_national_holiday_three_nights(
    zhen123_pricing, zhen123_special_dates
) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 5, 1),
        checkout_date=date(2026, 5, 4),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates=zhen123_special_dates,
    )
    out = render_quote_message(
        pricing=pricing,
        checkin_date=date(2026, 5, 1),
        checkout_date=date(2026, 5, 4),
        adult_count=4,
    )

    assert out.count("國定假日房價") == 3
    assert "連住折扣:-NT$2,000" in out
    assert "小計:NT$43,000" in out


def test_quote_confirmation_lines_in_order(zhen123_pricing) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        child_count=1,
        infant_count=1,
        pet_count=1,
        tenant_pricing=zhen123_pricing,
    )
    out = render_quote_message(
        pricing=pricing,
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        child_count=1,
        infant_count=1,
        pet_count=1,
    )

    safety_idx = out.index(SAFETY_NOTE)
    children_idx = out.index(CHILDREN_CONFIRMATION)
    infants_idx = out.index(INFANTS_CONFIRMATION)
    pets_idx = out.index(PETS_CONFIRMATION)

    assert safety_idx < children_idx < infants_idx < pets_idx


def test_quote_raises_when_cannot_quote(zhen123_pricing) -> None:
    pricing = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=17,
        tenant_pricing=zhen123_pricing,
    )

    with pytest.raises(ValueError):
        render_quote_message(
            pricing=pricing,
            checkin_date=date(2026, 5, 12),
            checkout_date=date(2026, 5, 13),
            adult_count=17,
        )


# --- render_missing_info_message ---


def test_missing_info_only_checkin() -> None:
    assert (
        render_missing_info_message(missing_checkin=True)
        == SINGLE_MISSING_CHECKIN_MESSAGE
    )


def test_missing_info_only_checkout() -> None:
    assert (
        render_missing_info_message(missing_checkout=True)
        == SINGLE_MISSING_CHECKOUT_MESSAGE
    )


def test_missing_info_only_guest_count() -> None:
    assert (
        render_missing_info_message(missing_guest_count=True)
        == SINGLE_MISSING_GUEST_COUNT_MESSAGE
    )


def test_missing_info_only_pet_count() -> None:
    assert (
        render_missing_info_message(missing_pet_count=True)
        == SINGLE_MISSING_PET_COUNT_MESSAGE
    )


def test_missing_info_checkout_and_guest_count() -> None:
    out = render_missing_info_message(
        missing_checkout=True,
        missing_guest_count=True,
    )

    assert MISSING_INFO_HEADER in out
    assert MISSING_CHECKOUT_LINE in out
    assert MISSING_GUEST_COUNT_LINE in out
    assert MISSING_INFO_FOOTER in out
    assert MISSING_CHECKIN_LINE not in out
    assert MISSING_PET_COUNT_LINE not in out


def test_missing_info_all_four_in_order() -> None:
    out = render_missing_info_message(
        missing_checkin=True,
        missing_checkout=True,
        missing_guest_count=True,
        missing_pet_count=True,
    )

    assert MISSING_CHECKIN_LINE in out
    assert MISSING_CHECKOUT_LINE in out
    assert MISSING_GUEST_COUNT_LINE in out
    assert MISSING_PET_COUNT_LINE in out
    assert (
        out.index(MISSING_CHECKIN_LINE)
        < out.index(MISSING_CHECKOUT_LINE)
        < out.index(MISSING_GUEST_COUNT_LINE)
        < out.index(MISSING_PET_COUNT_LINE)
    )


def test_missing_info_no_flags_raises() -> None:
    with pytest.raises(ValueError):
        render_missing_info_message()


# --- Simple constant messages ---


def test_render_over_capacity_message() -> None:
    assert render_over_capacity_message() == OVER_CAPACITY_MESSAGE


def test_render_invalid_date_message() -> None:
    assert render_invalid_date_message() == INVALID_DATE_MESSAGE


def test_render_full_house_message() -> None:
    assert render_full_house_message() == FULL_HOUSE_MESSAGE


def test_render_assumed_single_night_full_house_message() -> None:
    result = render_assumed_single_night_full_house_message(
        checkin_date=date(2026, 8, 15),
        checkout_date=date(2026, 8, 16),
    )

    assert result == (
        "您好,您詢問的入住 8/15、退房 8/16(住一晚)目前可能已有訂房,"
        "需請民宿人員和您確認是否仍有空房。若您的入住天數不只一晚,"
        "歡迎告訴我們正確的日期,我們再重新確認。"
    )


def test_render_missing_room_count_message() -> None:
    assert render_missing_room_count_message() == (
        "您好,請問您想開幾間房呢?(本館共 4 間房,4人房 2 間,2人房 2 間)"
    )


def test_render_room_capacity_suggestion_message() -> None:
    assert render_room_capacity_suggestion_message(
        guest_count=10,
        room_count=2,
        suggested_room_count=3,
    ) == "10 位的話,2 間房可能住不下喔,建議開 3 房,需要為您改成 3 房報價嗎?"


def test_render_manual_review_message() -> None:
    assert render_manual_review_message() == "您的需求我們請民宿人員為您進一步確認,稍後回覆您。"


# --- Owner push ---


def test_render_owner_push_full_house_no_name_is_friendly_and_id_free() -> None:
    out = render_owner_push_full_house(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
    )

    assert OWNER_PUSH_FULL_HOUSE_PREFIX in out
    assert "入住:2026/05/12(二)" in out
    assert "退房:2026/05/14(四)" in out
    assert "客人:" not in out  # no name -> customer line omitted


def test_render_owner_push_full_house_with_name_shows_customer_line() -> None:
    out = render_owner_push_full_house(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
        guest_count=4,
        display_name="王小姐",
    )

    assert "客人:王小姐" in out
    assert f"{OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_PREFIX}4" in out
    assert OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_UNKNOWN not in out


def test_render_owner_push_full_house_never_prints_userid() -> None:
    # KEY regression guard: a raw LINE userId must NEVER appear in an owner push.
    user_id = "Udd1dffaa94e003982bcb9e011655d4de"
    out = render_owner_push_full_house(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
        display_name=None,
    )

    assert user_id not in out


def test_render_owner_push_full_house_marks_guest_count_unknown() -> None:
    out = render_owner_push_full_house(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
    )

    assert OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_UNKNOWN in out


def test_render_owner_push_availability_unverified_no_name_is_friendly() -> None:
    from app.domain.reply_templates import render_owner_push_availability_unverified
    from app.domain.reply_text import OWNER_PUSH_AVAILABILITY_UNVERIFIED_PREFIX

    out = render_owner_push_availability_unverified(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
    )

    assert OWNER_PUSH_AVAILABILITY_UNVERIFIED_PREFIX in out
    assert "入住:2026/05/12(二)" in out
    assert "退房:2026/05/14(四)" in out
    assert "客人:" not in out


def test_render_owner_push_availability_unverified_with_name_shows_customer_line() -> None:
    from app.domain.reply_templates import render_owner_push_availability_unverified

    out = render_owner_push_availability_unverified(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 14),
        display_name="王小姐",
    )

    assert "客人:王小姐" in out


def test_render_owner_push_urgent_no_name_is_friendly_and_id_free() -> None:
    # No display name (today's only case): friendly 📩 urgent format, the
    # question text, trigger keywords, and NO contact line / NO raw id.
    out = render_owner_push_urgent(
        original_text="廚房水龍頭沒水了怎麼辦",
        matched_keywords=["沒水", "漏水"],
    )

    assert OWNER_PUSH_URGENT_PREFIX in out
    assert "【緊急】" in out  # urgency marker preserved
    assert "客人問:廚房水龍頭沒水了怎麼辦" in out
    assert "沒水, 漏水" in out
    assert "來自:" not in out  # old id-printing format is gone
    assert "客人:" not in out  # no name -> customer line omitted


def test_render_owner_push_urgent_with_name_shows_customer_line() -> None:
    out = render_owner_push_urgent(
        original_text="廚房水龍頭沒水了怎麼辦",
        matched_keywords=["沒水"],
        display_name="王小姐",
    )

    assert "客人:王小姐" in out
    assert "客人問:廚房水龍頭沒水了怎麼辦" in out


def test_render_owner_push_urgent_never_prints_userid() -> None:
    # KEY regression guard: a raw LINE userId must NEVER appear in an owner push.
    user_id = "Udd1dffaa94e003982bcb9e011655d4de"
    out = render_owner_push_urgent(
        original_text="火災", matched_keywords=["火災"], display_name=None
    )

    assert user_id not in out


def test_render_owner_push_uncategorized_no_name_is_friendly_and_id_free() -> None:
    out = render_owner_push_uncategorized(
        original_text="請問早餐幾點開始供應?",
    )

    assert OWNER_PUSH_UNCATEGORIZED_PREFIX in out
    assert "📩 有客人訊息待回覆" in out
    assert "客人問:請問早餐幾點開始供應?" in out
    assert "來自:" not in out  # old id-printing format is gone
    assert "客人:" not in out  # no name -> customer line omitted


def test_render_owner_push_uncategorized_unreplied_close_does_not_claim_reply() -> None:
    # Default / non-inquiry path: NO customer reply was sent, so the close must
    # NOT claim "系統已回覆" -- it must be the non-asserting hand-off close.
    out = render_owner_push_uncategorized(original_text="今天天氣不錯")

    assert "尚未回覆客人,請您接手" in out
    assert "系統已回覆" not in out  # truthfulness: no false reply claim


def test_render_owner_push_uncategorized_replied_close_claims_reply() -> None:
    # FAQ confirm-and-defer path: the system DID reply, so the truthful
    # "系統已回覆…" close is used.
    out = render_owner_push_uncategorized(
        original_text="有wifi嗎", customer_was_replied=True
    )

    assert "系統已回覆客人會請專人對接" in out
    assert "尚未回覆客人" not in out


def test_render_owner_push_uncategorized_with_name_shows_customer_line() -> None:
    out = render_owner_push_uncategorized(
        original_text="請問早餐幾點開始供應?",
        display_name="王小姐",
    )

    assert "客人:王小姐" in out
    assert "客人問:請問早餐幾點開始供應?" in out


def test_render_owner_push_uncategorized_never_prints_userid() -> None:
    # KEY regression guard: a raw LINE userId must NEVER appear in an owner push.
    user_id = "Udd1dffaa94e003982bcb9e011655d4de"
    out = render_owner_push_uncategorized(original_text="今天天氣不錯")

    assert user_id not in out


# --- Sanity / safety ---


def test_every_quote_message_contains_safety_note_exactly_once(
    zhen123_pricing, zhen123_special_dates
) -> None:
    scenarios = [
        dict(
            checkin_date=date(2026, 5, 12),
            checkout_date=date(2026, 5, 13),
            adult_count=4,
            tenant_pricing=zhen123_pricing,
        ),
        dict(
            checkin_date=date(2026, 5, 11),
            checkout_date=date(2026, 5, 13),
            adult_count=4,
            tenant_pricing=zhen123_pricing,
        ),
        dict(
            checkin_date=date(2026, 5, 12),
            checkout_date=date(2026, 5, 13),
            adult_count=2,
            child_count=2,
            tenant_pricing=zhen123_pricing,
        ),
        dict(
            checkin_date=date(2026, 5, 12),
            checkout_date=date(2026, 5, 13),
            adult_count=4,
            pet_count=2,
            tenant_pricing=zhen123_pricing,
        ),
        dict(
            checkin_date=date(2026, 2, 14),
            checkout_date=date(2026, 2, 23),
            adult_count=4,
            tenant_pricing=zhen123_pricing,
            tenant_special_dates=zhen123_special_dates,
        ),
    ]
    for s in scenarios:
        pricing = calculate_price(**s)
        out = render_quote_message(
            pricing=pricing,
            checkin_date=s["checkin_date"],
            checkout_date=s["checkout_date"],
            adult_count=s["adult_count"],
            child_count=s.get("child_count", 0),
            infant_count=s.get("infant_count", 0),
            pet_count=s.get("pet_count", 0),
        )
        assert out.count(SAFETY_NOTE) == 1


def test_no_render_function_outputs_guarantee_terms(zhen123_pricing) -> None:
    banned = ["確定", "保證", "已訂", "訂房成功", "有房", "確認有"]
    pricing = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        child_count=1,
        infant_count=1,
        pet_count=1,
        tenant_pricing=zhen123_pricing,
    )
    samples = [
        render_quote_message(
            pricing=pricing,
            checkin_date=date(2026, 5, 12),
            checkout_date=date(2026, 5, 13),
            adult_count=4,
            child_count=1,
            infant_count=1,
            pet_count=1,
        ),
        render_full_house_message(),
        render_over_capacity_message(),
        render_invalid_date_message(),
        render_missing_info_message(missing_checkin=True, missing_guest_count=True),
        render_owner_push_full_house(
            checkin_date=date(2026, 5, 12),
            checkout_date=date(2026, 5, 14),
        ),
    ]
    for out in samples:
        for term in banned:
            assert term not in out, f"banned term {term!r} found in: {out!r}"


def test_reply_text_module_has_no_app_imports() -> None:
    path = Path("app/domain/reply_text.py")
    source = path.read_text(encoding="utf-8")
    assert "def " not in source
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert stripped == "from typing import Final", (
                f"reply_text.py has unexpected import: {stripped!r}"
            )
