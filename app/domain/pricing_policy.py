from collections.abc import Iterator
from datetime import date, datetime, timedelta

from app.domain.pricing_models import NightlyPrice, PricingResult
from app.domain.room_policy import resolve_room_pricing_rule


_SUMMER_MONTHS = {7, 8}


def calculate_price(
    *,
    checkin_date: date,
    checkout_date: date,
    adult_count: int,
    child_count: int = 0,
    infant_count: int = 0,
    pet_count: int = 0,
    wants_bbq: bool = False,
    room_count: int,
    tenant_pricing: dict,
    room_policy: dict,
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
    max_capacity = _room_policy_max_capacity(room_policy)
    exceeds_max_capacity = max_capacity is not None and guest_count_used > max_capacity
    if exceeds_max_capacity:
        reasons.append("exceeds_max_capacity")

    room_rule = resolve_room_pricing_rule(
        room_count=room_count,
        room_policy=room_policy,
        tenant_pricing=tenant_pricing,
    )
    if room_count <= 0:
        reasons.append("invalid_room_count")
    elif room_rule is None:
        reasons.append("invalid_room_policy")
    elif not exceeds_max_capacity and guest_count_used > room_rule.max_capacity:
        reasons.append("room_capacity_exceeded")

    tier_key = room_rule.tier_key if room_rule is not None else None
    extra_person_fee = _extra_person_fee(room_count, guest_count_used, room_rule)

    if reasons:
        return PricingResult(
            can_quote=False,
            reasons=reasons,
            tier=tier_key,
            guest_count_used=guest_count_used if guest_count_used > 0 else None,
            room_count_used=room_count if room_count > 0 else None,
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
    bbq_fee = _bbq_cleaning_fee(tenant_pricing) if wants_bbq else 0
    long_stay_discount = max(0, nights - 1) * 1000
    total = room_subtotal + extra_person_fee + pet_fee + bbq_fee - long_stay_discount

    requires_owner_confirmation: list[str] = []
    if child_count > 0:
        requires_owner_confirmation.append("children")
    if infant_count > 0:
        requires_owner_confirmation.append("infants")
    if pet_count > 0:
        requires_owner_confirmation.append("pets")
    if wants_bbq:
        requires_owner_confirmation.append("bbq")

    return PricingResult(
        can_quote=True,
        tier=tier_key,
        guest_count_used=guest_count_used,
        room_count_used=room_count,
        nightly_prices=nightly_prices,
        room_subtotal=room_subtotal,
        long_stay_discount=long_stay_discount,
        extra_person_fee=extra_person_fee,
        pet_fee=pet_fee,
        bbq_fee=bbq_fee,
        total=total,
        requires_owner_confirmation=requires_owner_confirmation,
    )


def _extra_person_fee(room_count: int, guest_count: int, room_rule) -> int:
    if room_rule is None or room_count != 4:
        return 0
    if guest_count <= room_rule.standard_capacity:
        return 0
    if guest_count > room_rule.max_capacity:
        return 0
    return 1000 * (guest_count - room_rule.standard_capacity)


def _bbq_cleaning_fee(tenant_pricing: dict) -> int:
    bbq = tenant_pricing.get("bbq")
    if not isinstance(bbq, dict):
        return 0
    fee = bbq.get("cleaning_fee_twd")
    return fee if isinstance(fee, int) and not isinstance(fee, bool) and fee > 0 else 0


def _room_policy_max_capacity(room_policy: dict) -> int | None:
    value = room_policy.get("max_capacity")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


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
