from datetime import date

from pydantic import BaseModel, Field


class NightlyPrice(BaseModel):
    night_date: date
    price_type: str
    tier: str
    amount: int


class PricingResult(BaseModel):
    can_quote: bool
    reasons: list[str] = Field(default_factory=list)
    tier: str | None = None
    guest_count_used: int | None = None
    nightly_prices: list[NightlyPrice] = Field(default_factory=list)
    room_subtotal: int = 0
    long_stay_discount: int = 0
    extra_person_fee: int = 0
    pet_fee: int = 0
    total: int = 0
    requires_owner_confirmation: list[str] = Field(default_factory=list)
