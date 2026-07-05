from dataclasses import dataclass
from typing import Protocol


class LLMProviderError(Exception):
    reason = "http_error"


class LLMTimeoutError(LLMProviderError):
    reason = "timeout"


class LLMHTTPError(LLMProviderError):
    reason = "http_error"


class LLMParseError(LLMProviderError):
    reason = "parse_error"


class LLMFallbackExhaustedError(Exception):
    def __init__(
        self,
        message: str,
        *,
        primary_error: LLMProviderError,
        fallback_error: LLMProviderError,
    ) -> None:
        super().__init__(message)
        self.primary_error = primary_error
        self.fallback_error = fallback_error


@dataclass
class LLMOutput:
    intent: str | None
    checkin_date: str | None
    checkout_date: str | None
    adult_count: int | None
    child_count: int | None
    infant_count: int | None
    pet_count: int | None
    has_pet: bool | None
    last_message_text: str | None
    is_booking_intent: bool | None
    needs_clarification: bool
    clarification_reason: str | None
    room_count: int | None = None


class LLMProvider(Protocol):
    def parse(
        self,
        *,
        raw_text: str,
        reference_year: int,
        trigger: str,
        tenant_id: int,
    ) -> LLMOutput | None:
        """Return parsed slots; return None for timeout, bad JSON, or any failure."""
        ...
