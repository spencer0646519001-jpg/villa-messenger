from datetime import date

from pydantic import BaseModel, Field


class NightlyPrice(BaseModel):
    night_date: date
    price_type: str
    # Semantic types: weekday, saturday, summer_weekday,
    # summer_saturday_or_holiday, spring_festival, national_holiday.
    price_lookup_key: str
    # The actual key used to look up base_prices_per_night.
    # Per docs/pricing_rules.md V1.5, national_holiday maps to
    # "summer_saturday_or_holiday". V2 will introduce a dedicated holiday
    # price tier and this mapping will go away.
    tier: str
    amount: int


class PricingResult(BaseModel):
    can_quote: bool
    reasons: list[str] = Field(default_factory=list)
    tier: str | None = None
    guest_count_used: int | None = None
    room_count_used: int | None = None
    nightly_prices: list[NightlyPrice] = Field(default_factory=list)
    room_subtotal: int = 0
    long_stay_discount: int = 0
    extra_person_fee: int = 0
    pet_fee: int = 0
    bbq_fee: int = 0
    total: int = 0
    requires_owner_confirmation: list[str] = Field(default_factory=list)
