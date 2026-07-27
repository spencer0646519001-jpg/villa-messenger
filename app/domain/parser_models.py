from pydantic import BaseModel, Field


class GuestCountParseResult(BaseModel):
    adult_count: int | None = None
    child_count: int | None = None
    infant_count: int | None = None
    guest_count: int | None = None
    confidence: str = "low"
    needs_child_confirmation: bool = False
    needs_infant_confirmation: bool = False


class PetParseResult(BaseModel):
    has_pet: bool = False
    pet_count: int | None = None
    pet_type: str | None = None
    needs_pet_count_confirmation: bool = False


class DateParseResult(BaseModel):
    checkin_date: str | None = None
    checkout_date: str | None = None
    nights: int | None = None
    confidence: str = "low"
    missing_fields: list[str] = Field(default_factory=list)


class InquiryIntentResult(BaseModel):
    is_inquiry: bool
    inquiry_type: str | None = None


class InquiryParseResult(BaseModel):
    original_text: str
    intent: InquiryIntentResult
    dates: DateParseResult
    guests: GuestCountParseResult
    pets: PetParseResult
    room_count: int | None = None
    missing_fields: list[str] = Field(default_factory=list)
    can_preliminarily_quote: bool = False
    needs_clarification: bool = False
    clarification_reason: str | None = None
    matched_faq_topics: list[str] = Field(default_factory=list)
    llm_detected_intents: list[str] = Field(default_factory=list)
    availability_probe_checkout: str | None = None
    availability_probe_checkout_was_inferred: bool = False
