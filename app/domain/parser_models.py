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
    # True iff this message said ANYTHING about pets (affirm or explicit
    # negation) -- lets callers tell "customer said no pet" (has_pet=False,
    # mentioned=True) apart from "this message never brought up pets"
    # (has_pet=False, mentioned=False), which a bare has_pet=False cannot.
    mentioned: bool = False


class BbqParseResult(BaseModel):
    wants_bbq: bool = False
    # Same tri-state purpose as PetParseResult.mentioned, for BBQ.
    mentioned: bool = False


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
    bbq: BbqParseResult
    room_count: int | None = None
    missing_fields: list[str] = Field(default_factory=list)
    can_preliminarily_quote: bool = False
    needs_clarification: bool = False
    clarification_reason: str | None = None
    matched_faq_topics: list[str] = Field(default_factory=list)
    llm_detected_intents: list[str] = Field(default_factory=list)
    availability_probe_checkout: str | None = None
    availability_probe_checkout_was_inferred: bool = False
    # True only when TYPE_2_INTENT_JUDGMENT explicitly asked the LLM and it
    # said this is NOT a booking intent -- distinct from intent staying
    # "unknown" because no judgment was ever made (e.g. eval's rule-only
    # mode). Lets downstream state-open gates tell "confidently rejected"
    # apart from "genuinely unclassified".
    llm_rejected_booking_intent: bool = False
