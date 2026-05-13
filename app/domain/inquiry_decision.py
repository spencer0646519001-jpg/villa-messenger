"""
InquiryDecision — the output of InquiryService.handle_message().

A decision object describing what the orchestration layer concluded.
The caller (PR8) is responsible for executing side effects:
  - storing log_payload to DB if present
  - sending customer_reply_text via LINE if present
  - sending owner_push_text via LINE if present

This module has zero I/O and zero dependencies on app code.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


ActionType = Literal[
    "reply_to_customer_only",
    "push_to_owner_only",
    "reply_and_push",
    "do_nothing",
    "push_owner_urgent",
]


# Expected (has_customer_reply, has_owner_push) per action_type.
_ACTION_TEXT_SHAPE: dict[str, tuple[bool, bool]] = {
    "reply_to_customer_only": (True, False),
    "push_to_owner_only":     (False, True),
    "reply_and_push":         (True, True),
    "do_nothing":             (False, False),
    "push_owner_urgent":      (False, True),
}


class InquiryDecision(BaseModel):
    action_type: ActionType
    customer_reply_text: str | None = None
    owner_push_text: str | None = None
    log_payload: dict = Field(default_factory=dict)
    was_urgent: bool = False
    was_system_off: bool = False
    parsed_as_inquiry: bool = False
    could_quote: bool = False

    @model_validator(mode="after")
    def _validate_invariants(self) -> "InquiryDecision":
        self._validate_text_shape()
        self._validate_urgent_flag()
        self._validate_off_flag()
        if not self.log_payload:
            raise ValueError("log_payload must be non-empty")
        return self

    def _validate_text_shape(self) -> None:
        has_reply = self.customer_reply_text is not None
        has_push = self.owner_push_text is not None
        if (has_reply, has_push) != _ACTION_TEXT_SHAPE[self.action_type]:
            raise ValueError(
                f"action_type={self.action_type!r}: invalid text combination "
                f"reply={has_reply}, push={has_push}"
            )

    def _validate_urgent_flag(self) -> None:
        if self.was_urgent != (self.action_type == "push_owner_urgent"):
            raise ValueError(
                "was_urgent must be True iff action_type == 'push_owner_urgent'"
            )

    def _validate_off_flag(self) -> None:
        if not self.was_system_off:
            return
        if self.action_type != "do_nothing":
            raise ValueError("was_system_off requires action_type == 'do_nothing'")
        if self.was_urgent:
            raise ValueError("was_system_off cannot coexist with was_urgent")
