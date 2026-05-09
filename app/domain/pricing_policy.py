from collections.abc import Iterator
from datetime import date, datetime, timedelta

from app.domain.pricing_models import NightlyPrice, PricingResult


_SUMMER_MONTHS = {7, 8}


def calculate_price(
    *,
    checkin_date: date,
    checkout_date: date,
    adult_count: int,
    child_count: int = 0,
    infant_count: int = 0,
    pet_count: int = 0,
    tenant_pricing: dict,
    tenant_special_dates: dict | None = None,
) -> PricingResult:
    reasons: list[str] = []

    if adult_count < 0 or child_count < 0 or infant_count < 0 or pet_count < 0:
        reasons.append("invalid_input")
    if checkout_date <= checkin_date:
        reasons.append("invalid_date_range")

    guest_count_used = adult_count + child_count
    if guest_count_used <= 0:
        reasons.append("no_guest_count")
    elif guest_count_used >= 17:
        reasons.append("exceeds_max_capacity")

    tier_key, extra_person_fee = _resolve_tier(guest_count_used)

    if reasons:
        return PricingResult(
            can_quote=False,
            reasons=reasons,
            tier=tier_key,
            guest_count_used=guest_count_used if guest_count_used > 0 else None,
        )

    national_holidays, spring_festival = _load_special_dates(tenant_special_dates)

    nightly_prices: list[NightlyPrice] = []
    for night in _iter_nights(checkin_date, checkout_date):
        price_type, price_lookup_key = _resolve_price_type(
            night, national_holidays, spring_festival
        )
        amount = tenant_pricing["base_prices_per_night"][tier_key][price_lookup_key]
        nightly_prices.append(
            NightlyPrice(
                night_date=night,
                price_type=price_type,
                price_lookup_key=price_lookup_key,
                tier=tier_key,
                amount=amount,
            )
        )

    nights = len(nightly_prices)
    room_subtotal = sum(n.amount for n in nightly_prices)
    pet_fee = 500 * pet_count if pet_count > 0 else 0
    long_stay_discount = max(0, nights - 1) * 1000
    total = room_subtotal + extra_person_fee + pet_fee - long_stay_discount

    requires_owner_confirmation: list[str] = []
    if child_count > 0:
        requires_owner_confirmation.append("children")
    if infant_count > 0:
        requires_owner_confirmation.append("infants")
    if pet_count > 0:
        requires_owner_confirmation.append("pets")

    return PricingResult(
        can_quote=True,
        tier=tier_key,
        guest_count_used=guest_count_used,
        nightly_prices=nightly_prices,
        room_subtotal=room_subtotal,
        long_stay_discount=long_stay_discount,
        extra_person_fee=extra_person_fee,
        pet_fee=pet_fee,
        total=total,
        requires_owner_confirmation=requires_owner_confirmation,
    )


def _resolve_tier(guest_count: int) -> tuple[str | None, int]:
    if guest_count <= 0:
        return None, 0
    if guest_count <= 8:
        return "8_people", 0
    if guest_count <= 10:
        return "10_people", 0
    if guest_count <= 12:
        return "12_people", 0
    if guest_count <= 16:
        return "12_people", 1000 * (guest_count - 12)
    return None, 0


def _iter_nights(checkin: date, checkout: date) -> Iterator[date]:
    current = checkin
    while current < checkout:
        yield current
        current = current + timedelta(days=1)


def _load_special_dates(raw: dict | None) -> tuple[set[date], set[date]]:
    if raw is None:
        return set(), set()
    # Ignore self-doc fields like "_note" by filtering keys starting with "_".
    cleaned = {k: v for k, v in raw.items() if not k.startswith("_")}
    national = {_parse_iso_date(s) for s in cleaned.get("national_holidays", [])}
    spring = {_parse_iso_date(s) for s in cleaned.get("spring_festival", [])}
    return national, spring


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _resolve_price_type(
    night: date,
    national_holidays: set[date],
    spring_festival: set[date],
) -> tuple[str, str]:
    # V1.5: if a date appears in both spring_festival and national_holidays,
    # spring_festival wins by priority. We do not validate against this overlap;
    # tenant config is trusted at this layer. Consider validation in admin UI (V3).
    if night in spring_festival:
        return "spring_festival", "spring_festival"
    if night in national_holidays:
        return "national_holiday", "summer_saturday_or_holiday"

    is_summer = night.month in _SUMMER_MONTHS
    is_saturday = night.weekday() == 5
    if is_summer and is_saturday:
        return "summer_saturday_or_holiday", "summer_saturday_or_holiday"
    if is_summer:
        return "summer_weekday", "summer_weekday"
    if is_saturday:
        return "saturday", "saturday"
    return "weekday", "weekday"
