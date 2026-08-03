import json
from datetime import date
from pathlib import Path

import pytest

from app.domain.pricing_policy import calculate_price as _calculate_price


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
    assert result.long_stay_discount == 1000
    assert result.total == 17000


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
    assert result.total == 23000


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
    assert result.total == 26000


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


def test_adults_plus_bbq_weekday_night(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        wants_bbq=True,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.room_subtotal == 9000
    assert result.bbq_fee == 1000
    assert result.total == 10000
    assert result.requires_owner_confirmation == ["bbq"]


def test_bbq_fee_is_flat_not_per_night(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 11),
        checkout_date=date(2026, 5, 14),
        adult_count=4,
        wants_bbq=True,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert len(result.nightly_prices) == 3
    assert result.bbq_fee == 1000


def test_no_bbq_fee_when_not_requested(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.bbq_fee == 0
    assert "bbq" not in result.requires_owner_confirmation


def test_all_confirmation_flags_including_bbq_in_order(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        child_count=1,
        infant_count=1,
        pet_count=1,
        wants_bbq=True,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.requires_owner_confirmation == ["children", "infants", "pets", "bbq"]


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


# --- PR5b: holidays + long-stay discount ---


@pytest.fixture
def zhen123_special_dates():
    config_path = Path("data/tenants/zhen123-house/config.json")
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)["special_dates"]


def test_pr5a_backward_compat_without_special_dates(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.tier == "8_people"
    assert result.room_subtotal == 9000
    assert result.total == 9000
    assert result.long_stay_discount == 0
    assert result.nightly_prices[0].price_type == "weekday"
    assert result.nightly_prices[0].price_lookup_key == "weekday"


def test_empty_special_dates_same_as_pr5a(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates={"national_holidays": [], "spring_festival": []},
    )

    assert result.can_quote is True
    assert result.total == 9000
    assert result.nightly_prices[0].price_type == "weekday"
    assert result.nightly_prices[0].price_lookup_key == "weekday"


def test_note_field_is_ignored_gracefully(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 1),
        checkout_date=date(2026, 5, 2),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates={
            "_note": "test",
            "national_holidays": ["2026-05-01"],
            "spring_festival": [],
        },
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "national_holiday"
    assert result.nightly_prices[0].price_lookup_key == "summer_saturday_or_holiday"
    assert result.nightly_prices[0].amount == 15000
    assert result.total == 15000


def test_national_holiday_weekday_real_fixture(zhen123_pricing, zhen123_special_dates) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 1),
        checkout_date=date(2026, 5, 2),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates=zhen123_special_dates,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "national_holiday"
    assert result.nightly_prices[0].price_lookup_key == "summer_saturday_or_holiday"
    assert result.nightly_prices[0].amount == 15000
    assert result.total == 15000
    assert result.long_stay_discount == 0


def test_national_holiday_sunday_real_fixture(zhen123_pricing, zhen123_special_dates) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 3),
        checkout_date=date(2026, 5, 4),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates=zhen123_special_dates,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "national_holiday"
    assert result.nightly_prices[0].price_lookup_key == "summer_saturday_or_holiday"
    assert result.nightly_prices[0].amount == 15000
    assert result.total == 15000


def test_national_holiday_saturday_beats_saturday(zhen123_pricing, zhen123_special_dates) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 2),
        checkout_date=date(2026, 5, 3),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates=zhen123_special_dates,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "national_holiday"
    assert result.nightly_prices[0].price_lookup_key == "summer_saturday_or_holiday"
    assert result.nightly_prices[0].amount == 15000


def test_spring_festival_single_night_8_people(zhen123_pricing, zhen123_special_dates) -> None:
    result = calculate_price(
        checkin_date=date(2026, 2, 17),
        checkout_date=date(2026, 2, 18),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates=zhen123_special_dates,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "spring_festival"
    assert result.nightly_prices[0].price_lookup_key == "spring_festival"
    assert result.nightly_prices[0].amount == 25000
    assert result.total == 25000


def test_spring_festival_10_people_tier(zhen123_pricing, zhen123_special_dates) -> None:
    result = calculate_price(
        checkin_date=date(2026, 2, 17),
        checkout_date=date(2026, 2, 18),
        adult_count=9,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates=zhen123_special_dates,
    )

    assert result.can_quote is True
    assert result.tier == "10_people"
    assert result.nightly_prices[0].price_type == "spring_festival"
    assert result.nightly_prices[0].amount == 28000


def test_spring_festival_12_people_tier(zhen123_pricing, zhen123_special_dates) -> None:
    result = calculate_price(
        checkin_date=date(2026, 2, 17),
        checkout_date=date(2026, 2, 18),
        adult_count=11,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates=zhen123_special_dates,
    )

    assert result.can_quote is True
    assert result.tier == "12_people"
    assert result.nightly_prices[0].price_type == "spring_festival"
    assert result.nightly_prices[0].amount == 31000


def test_spring_festival_beats_national_holiday_priority(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 2, 17),
        checkout_date=date(2026, 2, 18),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates={
            "national_holidays": ["2026-02-17"],
            "spring_festival": ["2026-02-17"],
        },
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "spring_festival"
    assert result.nightly_prices[0].price_lookup_key == "spring_festival"
    assert result.nightly_prices[0].amount == 25000


def test_national_holiday_during_summer(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 7, 15),
        checkout_date=date(2026, 7, 16),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates={
            "national_holidays": ["2026-07-15"],
            "spring_festival": [],
        },
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "national_holiday"
    assert result.nightly_prices[0].price_lookup_key == "summer_saturday_or_holiday"
    assert result.nightly_prices[0].amount == 15000


def test_long_stay_discount_two_weekday_nights(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 11),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.room_subtotal == 18000
    assert result.long_stay_discount == 1000
    assert result.total == 17000


def test_long_stay_discount_three_weekday_nights(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 11),
        checkout_date=date(2026, 5, 14),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.room_subtotal == 27000
    assert result.long_stay_discount == 2000
    assert result.total == 25000


def test_long_stay_discount_five_nights(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 11),
        checkout_date=date(2026, 5, 16),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert len(result.nightly_prices) == 5
    assert result.long_stay_discount == 4000


def test_long_stay_discount_with_extras(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 11),
        checkout_date=date(2026, 5, 13),
        adult_count=13,
        pet_count=2,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.room_subtotal == 30000
    assert result.extra_person_fee == 1000
    assert result.pet_fee == 1000
    assert result.long_stay_discount == 1000
    assert result.total == 31000


def test_long_stay_discount_with_mixed_price_types(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 8),
        checkout_date=date(2026, 5, 10),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_type == "weekday"
    assert result.nightly_prices[1].price_type == "saturday"
    assert result.room_subtotal == 24000
    assert result.long_stay_discount == 1000
    assert result.total == 23000


def test_long_stay_discount_across_spring_festival(zhen123_pricing, zhen123_special_dates) -> None:
    result = calculate_price(
        checkin_date=date(2026, 2, 16),
        checkout_date=date(2026, 2, 19),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates=zhen123_special_dates,
    )

    assert result.can_quote is True
    assert len(result.nightly_prices) == 3
    assert all(n.price_type == "spring_festival" for n in result.nightly_prices)
    assert result.room_subtotal == 75000
    assert result.long_stay_discount == 2000
    assert result.total == 73000


def test_real_fixture_spring_festival_full_9_night_stay(
    zhen123_pricing, zhen123_special_dates
) -> None:
    result = calculate_price(
        checkin_date=date(2026, 2, 14),
        checkout_date=date(2026, 2, 23),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates=zhen123_special_dates,
    )

    assert result.can_quote is True
    assert len(result.nightly_prices) == 9
    assert all(n.price_type == "spring_festival" for n in result.nightly_prices)
    assert result.room_subtotal == 225000
    assert result.long_stay_discount == 8000
    assert result.total == 217000


def test_real_fixture_national_holiday_connected_weekend(
    zhen123_pricing, zhen123_special_dates
) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 1),
        checkout_date=date(2026, 5, 4),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
        tenant_special_dates=zhen123_special_dates,
    )

    assert result.can_quote is True
    assert len(result.nightly_prices) == 3
    assert all(n.price_type == "national_holiday" for n in result.nightly_prices)
    assert all(
        n.price_lookup_key == "summer_saturday_or_holiday" for n in result.nightly_prices
    )
    assert result.room_subtotal == 45000
    assert result.long_stay_discount == 2000
    assert result.total == 43000


def test_can_quote_false_keeps_long_stay_discount_zero(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=17,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is False
    assert result.long_stay_discount == 0
    assert result.total == 0


def test_pr5a_weekday_price_lookup_key_regression(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.nightly_prices[0].price_lookup_key == "weekday"


def test_room_count_selects_tier_even_when_guest_count_is_low(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=4,
        room_count=3,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.tier == "10_people"
    assert result.room_count_used == 3
    assert result.total == 12000


def test_four_rooms_thirteen_guests_adds_extra_person_fee(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 7, 28),
        checkout_date=date(2026, 7, 29),
        adult_count=13,
        room_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.room_subtotal == 18000
    assert result.extra_person_fee == 1000
    assert result.total == 19000


def test_four_rooms_twelve_guests_summer_weekday_has_no_extra_fee(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 7, 28),
        checkout_date=date(2026, 7, 29),
        adult_count=12,
        room_count=4,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is True
    assert result.tier == "12_people"
    assert result.room_subtotal == 18000
    assert result.extra_person_fee == 0
    assert result.total == 18000


def test_two_rooms_ten_guests_is_defensively_unquotable(zhen123_pricing) -> None:
    result = calculate_price(
        checkin_date=date(2026, 5, 12),
        checkout_date=date(2026, 5, 13),
        adult_count=10,
        room_count=2,
        tenant_pricing=zhen123_pricing,
    )

    assert result.can_quote is False
    assert "room_capacity_exceeded" in result.reasons
