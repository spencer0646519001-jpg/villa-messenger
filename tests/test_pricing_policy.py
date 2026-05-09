import json
from datetime import date
from pathlib import Path

import pytest

from app.domain.pricing_policy import calculate_price


@pytest.fixture
def zhen123_pricing():
    config_path = Path("data/tenants/zhen123-house/config.json")
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)["pricing"]


def test_8_person_tier_one_weekday_night(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.tier == "8_people"
    assert result.guest_count_used == 4
    assert result.room_subtotal == 9000
    assert result.total == 9000
    assert len(result.nightly_prices) == 1
    assert result.nightly_prices[0].price_type == "weekday"
    assert result.nightly_prices[0].amount == 9000
    assert result.nightly_prices[0].tier == "8_people"
    assert result.requires_owner_confirmation == []


def test_8_person_tier_one_saturday_night(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 9),
        checkout_date=date(2026, 5, 10),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.total == 15000
    assert result.nightly_prices[0].price_type == "saturday"


def test_10_person_tier_weekday_night(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=9,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.tier == "10_people"
    assert result.total == 12000


def test_12_person_tier_weekday_night(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=11,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.tier == "12_people"
    assert result.total == 15000


def test_13_guests_weekday_night_extra_person_fee(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=13,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.tier == "12_people"
    assert result.room_subtotal == 15000
    assert result.extra_person_fee == 1000
    assert result.total == 16000


def test_16_guests_weekday_night_extra_person_fee(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=16,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.tier == "12_people"
    assert result.room_subtotal == 15000
    assert result.extra_person_fee == 4000
    assert result.total == 19000


def test_17_guests_exceeds_max_capacity(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=17,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is False
    assert result.reasons == ["exceeds_max_capacity"]
    assert result.total == 0
    assert result.nightly_prices == []


def test_zero_guests_no_guest_count(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=0,
        child_count=0,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is False
    assert result.reasons == ["no_guest_count"]


def test_checkout_equals_checkin_invalid_date_range(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 12),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is False
    assert result.reasons == ["invalid_date_range"]


def test_checkout_before_checkin_invalid_date_range(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 14),
        checkout_date=date(2026, 5, 12),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is False
    assert result.reasons == ["invalid_date_range"]


def test_negative_adult_count_invalid_input(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=-1,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is False
    assert "invalid_input" in result.reasons


def test_two_weekday_nights_no_long_stay_discount_in_pr5a(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 11),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert len(result.nightly_prices) == 2
    assert all(n.price_type == "weekday" for n in result.nightly_prices)
    assert result.room_subtotal == 18000
    assert result.long_stay_discount == 0
    assert result.total == 18000


def test_friday_saturday_stay(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 8),
        checkout_date=date(2026, 5, 10),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "weekday"
    assert result.nightly_prices[0].amount == 9000
    assert result.nightly_prices[1].price_type == "saturday"
    assert result.nightly_prices[1].amount == 15000
    assert result.room_subtotal == 24000
    assert result.total == 24000


def test_summer_weekday(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 7, 15),
        checkout_date=date(2026, 7, 16),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "summer_weekday"
    assert result.total == 12000


def test_summer_saturday(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 7, 18),
        checkout_date=date(2026, 7, 19),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "summer_saturday_or_holiday"
    assert result.total == 15000


def test_mixed_summer_friday_to_sunday_crossing_month_boundary(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 7, 31),
        checkout_date=date(2026, 8, 2),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "summer_weekday"
    assert result.nightly_prices[0].amount == 12000
    assert result.nightly_prices[1].price_type == "summer_saturday_or_holiday"
    assert result.nightly_prices[1].amount == 15000
    assert result.room_subtotal == 27000
    assert result.total == 27000


def test_adults_plus_children_weekday_night(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=8,
        child_count=2,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.guest_count_used == 10
    assert result.tier == "10_people"
    assert result.total == 12000
    assert result.requires_owner_confirmation == ["children"]


def test_adults_plus_infant_weekday_night(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=8,
        infant_count=1,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.guest_count_used == 8
    assert result.tier == "8_people"
    assert result.total == 9000
    assert result.requires_owner_confirmation == ["infants"]


def test_adults_plus_pets_weekday_night(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        pet_count=2,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.room_subtotal == 9000
    assert result.pet_fee == 1000
    assert result.total == 10000
    assert result.requires_owner_confirmation == ["pets"]


def test_all_confirmation_flags_in_order(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        child_count=1,
        infant_count=1,
        pet_count=1,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.requires_owner_confirmation == ["children", "infants", "pets"]


def test_pet_fee_is_per_stay_not_per_night(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 11),
        checkout_date=date(2026, 5, 14),
        adult_count=4,
        pet_count=1,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert len(result.nightly_prices) == 3
    assert result.pet_fee == 500


def test_sunday_uses_weekday_rate(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 10),
        checkout_date=date(2026, 5, 11),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "weekday"
    assert result.total == 9000


def test_non_summer_weekday_is_not_summer_pricing(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 6, 17),
        checkout_date=date(2026, 6, 18),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "weekday"


def test_summer_boundary_june_30_is_not_summer(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 6, 30),
        checkout_date=date(2026, 7, 1),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "weekday"


def test_summer_boundary_july_1_is_summer(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 7, 1),
        checkout_date=date(2026, 7, 2),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "summer_weekday"
