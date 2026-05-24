from datetime import date

from app.domain.pricing_models import PricingResult
from app.domain.reply_text import (
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
    OWNER_PUSH_AVAILABILITY_UNVERIFIED_PREFIX,
    OWNER_PUSH_FULL_HOUSE_PREFIX,
    OWNER_PUSH_UNCATEGORIZED_PREFIX,
    OWNER_PUSH_URGENT_PREFIX,
    PETS_CONFIRMATION,
    PRICE_TYPE_LABEL,
    QUOTE_GREETING,
    SAFETY_NOTE,
    SINGLE_MISSING_CHECKIN_MESSAGE,
    SINGLE_MISSING_CHECKOUT_MESSAGE,
    SINGLE_MISSING_GUEST_COUNT_MESSAGE,
    SINGLE_MISSING_PET_COUNT_MESSAGE,
    WEEKDAY_ZH,
)

_COST_DETAIL_HEADER = "—— 費用明細 ——"
_COST_DETAIL_DIVIDER = "—————————"


def _format_date_with_weekday(d: date) -> str:
    return f"{d.year}/{d.month:02d}/{d.day:02d}({WEEKDAY_ZH[d.weekday()]})"


def _format_money(amount: int) -> str:
    return f"NT${amount:,}"


def _format_guest_summary(adults: int, children: int = 0, infants: int = 0) -> str:
    total = adults + children
    if children == 0 and infants == 0:
        return f"{adults} 大人(共 {total} 人)"
    parts = [f"{adults} 大"]
    if children > 0:
        parts.append(f"{children} 小")
    if infants > 0:
        parts.append(f"{infants} 嬰")
    return f"{' '.join(parts)}(共 {total} 人)"


def render_quote_message(
    *,
    pricing: PricingResult,
    checkin_date: date,
    checkout_date: date,
    adult_count: int,
    child_count: int = 0,
    infant_count: int = 0,
    pet_count: int = 0,
) -> str:
    if not pricing.can_quote:
        raise ValueError("render_quote_message requires can_quote=True")

    nights = len(pricing.nightly_prices)
    lines: list[str] = [QUOTE_GREETING, ""]
    lines.append(f"入住:{_format_date_with_weekday(checkin_date)}")
    lines.append(f"退房:{_format_date_with_weekday(checkout_date)}")
    lines.append(f"共 {nights} 晚")
    lines.append("")
    lines.append(f"住宿人數:{_format_guest_summary(adult_count, child_count, infant_count)}")
    if pet_count > 0:
        lines.append(f"寵物:{pet_count} 隻")
    lines.append("房型:包棟")
    lines.append("")
    lines.append(_COST_DETAIL_HEADER)
    for n in pricing.nightly_prices:
        label = PRICE_TYPE_LABEL[n.price_type]
        lines.append(
            f"{label}({n.night_date.month}/{n.night_date.day}):{_format_money(n.amount)}"
        )
    if pricing.long_stay_discount > 0:
        lines.append(f"連住折扣:-{_format_money(pricing.long_stay_discount)}")
    if pricing.extra_person_fee > 0:
        lines.append(f"加人費:{_format_money(pricing.extra_person_fee)}")
    if pricing.pet_fee > 0:
        lines.append(f"寵物清潔費:{_format_money(pricing.pet_fee)}")
    lines.append(_COST_DETAIL_DIVIDER)
    lines.append(f"小計:{_format_money(pricing.total)}")
    lines.append("")
    lines.append(SAFETY_NOTE)

    if "children" in pricing.requires_owner_confirmation:
        lines.append("")
        lines.append(CHILDREN_CONFIRMATION)
    if "infants" in pricing.requires_owner_confirmation:
        lines.append("")
        lines.append(INFANTS_CONFIRMATION)
    if "pets" in pricing.requires_owner_confirmation:
        lines.append("")
        lines.append(PETS_CONFIRMATION)

    return "\n".join(lines)


def render_missing_info_message(
    *,
    missing_checkin: bool = False,
    missing_checkout: bool = False,
    missing_guest_count: bool = False,
    missing_pet_count: bool = False,
) -> str:
    flags = [missing_checkin, missing_checkout, missing_guest_count, missing_pet_count]
    count = sum(1 for f in flags if f)
    if count == 0:
        raise ValueError("at least one missing field required")
    if count == 1:
        if missing_checkin:
            return SINGLE_MISSING_CHECKIN_MESSAGE
        if missing_checkout:
            return SINGLE_MISSING_CHECKOUT_MESSAGE
        if missing_guest_count:
            return SINGLE_MISSING_GUEST_COUNT_MESSAGE
        return SINGLE_MISSING_PET_COUNT_MESSAGE

    lines: list[str] = [MISSING_INFO_HEADER, ""]
    if missing_checkin:
        lines.append(MISSING_CHECKIN_LINE)
    if missing_checkout:
        lines.append(MISSING_CHECKOUT_LINE)
    if missing_guest_count:
        lines.append(MISSING_GUEST_COUNT_LINE)
    if missing_pet_count:
        lines.append(MISSING_PET_COUNT_LINE)
    lines.append("")
    lines.append(MISSING_INFO_FOOTER)
    return "\n".join(lines)


def render_over_capacity_message() -> str:
    return OVER_CAPACITY_MESSAGE


def render_invalid_date_message() -> str:
    return INVALID_DATE_MESSAGE


def render_full_house_message() -> str:
    return FULL_HOUSE_MESSAGE


def render_owner_push_full_house(
    *,
    checkin_date: date,
    checkout_date: date,
    inquiry_id: int | None = None,
) -> str:
    lines = [OWNER_PUSH_FULL_HOUSE_PREFIX]
    if inquiry_id is not None:
        lines.append(f"詢價編號:#{inquiry_id}")
    lines.append(f"入住:{_format_date_with_weekday(checkin_date)}")
    lines.append(f"退房:{_format_date_with_weekday(checkout_date)}")
    return "\n".join(lines)


def render_owner_push_availability_unverified(
    *,
    checkin_date: date,
    checkout_date: date,
) -> str:
    return "\n".join(
        [
            OWNER_PUSH_AVAILABILITY_UNVERIFIED_PREFIX,
            f"入住:{_format_date_with_weekday(checkin_date)}",
            f"退房:{_format_date_with_weekday(checkout_date)}",
        ]
    )


def render_owner_push_urgent(
    *,
    original_text: str,
    matched_keywords: list[str],
    contact_display: str,
) -> str:
    return "\n".join(
        [
            OWNER_PUSH_URGENT_PREFIX,
            f"來自:{contact_display}",
            f"觸發關鍵字:{', '.join(matched_keywords)}",
            "原文:",
            original_text,
        ]
    )


def render_owner_push_uncategorized(
    *,
    original_text: str,
    contact_display: str,
) -> str:
    return "\n".join(
        [
            OWNER_PUSH_UNCATEGORIZED_PREFIX,
            f"來自:{contact_display}",
            "原文:",
            original_text,
        ]
    )
