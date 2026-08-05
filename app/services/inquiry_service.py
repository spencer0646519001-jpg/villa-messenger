"""
InquiryService — orchestrates one inbound customer message through the
V1.5 decision pipeline. Returns an InquiryDecision; the caller (PR8)
executes side effects (DB writes, LINE sends).

Allowed imports: stdlib, pydantic, app.domain.*, app.schemas, plus
app.services.availability_service (the anti-corruption boundary that
hides Google API details from this orchestrator).
Forbidden imports: app.repositories, app.api, app.adapters, app.clients.
"""

from datetime import date, datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from app.domain.availability_probe import with_single_night_availability_probe
from app.domain.availability_gate import (
    AvailabilityGateResult,
    evaluate_availability_gate,
)
from app.domain.inquiry_decision import InquiryDecision
from app.domain.inquiry_parser import parse_inquiry
from app.domain.llm_fallback import llm_fallback_parse
from app.domain.llm_provider import LLMProvider
from app.domain.parser_models import InquiryParseResult
from app.domain.pricing_models import PricingResult
from app.domain.pricing_policy import calculate_price
from app.domain.reply_templates import (
    render_assumed_single_night_full_house_message,
    render_date_range_clarification_message,
    render_full_house_message,
    render_invalid_date_message,
    render_manual_review_message,
    render_missing_info_message,
    render_missing_room_count_message,
    render_over_capacity_message,
    render_owner_push_availability_unverified,
    render_owner_push_full_house,
    render_owner_push_uncategorized,
    render_owner_push_urgent,
    render_quote_message,
    render_room_capacity_suggestion_message,
)
from app.domain.room_policy import (
    max_guest_capacity,
    minimum_rooms_for_guest_count,
    resolve_room_pricing_rule,
)
from app.domain.urgency_detector import UrgencyDetectionResult, detect_urgency
from app.schemas import InboundMessage
from app.services.availability_service import AvailabilityService


# Night window in tenant-local time: [_NIGHT_START_HOUR, 24) U [0, _NIGHT_END_HOUR).
# Duplicates the schema default for tenant_operation_state.auto_on_start_time /
# auto_on_end_time ('23:00' / '08:00') so is_night aligns with the auto-on window.
# V2 admin UI work will unify these so the value lives in one place.
_NIGHT_START_HOUR = 23
_NIGHT_END_HOUR = 8


_QUOTE_RELEVANT_INTENTS = {"price", "availability", "booking_question"}


_OPTIONAL_LOG_FIELDS: tuple[str, ...] = (
    "urgency_category",
    "urgency_matched_keywords",
    "inquiry_intent",
    "parsed_checkin",
    "parsed_checkout",
    "parsed_adult_count",
    "parsed_child_count",
    "parsed_infant_count",
    "parsed_room_count",
    "parsed_pet_count",
    "parsed_has_pet",
    "parsed_wants_bbq",
    "quoted_total",
    "missing_fields",
    "matched_faq_topics",
    "llm_detected_intents",
    "availability_probe_checkout",
    "availability_probe_checkout_was_inferred",
)


class InquiryService:
    def __init__(
        self,
        *,
        operation_mode_service,
        tenant_pricing_loader: Callable[[int], dict],
        tenant_special_dates_loader: Callable[[int], dict],
        tenant_room_policy_loader: Callable[[int], dict] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        availability_service: AvailabilityService | None = None,
        llm_provider: LLMProvider | None = None,
        conversation_handoff_service=None,
    ) -> None:
        self._operation_mode_service = operation_mode_service
        self._conversation_handoff_service = conversation_handoff_service
        self._tenant_pricing_loader = tenant_pricing_loader
        self._tenant_special_dates_loader = tenant_special_dates_loader
        self._tenant_room_policy_loader = tenant_room_policy_loader or (
            lambda tenant_id: {}
        )
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._availability_service = availability_service
        self._llm_provider = llm_provider

    def handle_message(self, *, message: InboundMessage) -> InquiryDecision:
        urgency = detect_urgency(message.text)
        if urgency.is_urgent:
            return self._handle_urgent(message, urgency)
        reference_year = self._reference_year()
        inquiry = parse_inquiry(message.text, reference_year=reference_year)
        system_state = self._system_state(message)
        if system_state != "on":
            return self._handle_off_mode(message, inquiry, system_state)
        inquiry = self._with_llm_fallback(message, inquiry, reference_year)
        inquiry = with_single_night_availability_probe(inquiry, message.text)
        if not self._is_quote_relevant(inquiry):
            return self._handle_non_inquiry(message, inquiry)
        return self._handle_quote_relevant(message, inquiry)

    def _handle_quote_relevant(
        self, message: InboundMessage, inquiry: InquiryParseResult
    ) -> InquiryDecision:
        probe_block = self._handle_probe_if_blocked(message, inquiry)
        if probe_block is not None:
            return probe_block
        if inquiry.needs_clarification or inquiry.missing_fields:
            return self._handle_missing_info(message, inquiry)
        return self._handle_pricing(message, inquiry)

    def _reference_year(self) -> int:
        return self._now().year

    def _with_llm_fallback(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
        reference_year: int,
    ) -> InquiryParseResult:
        return llm_fallback_parse(
            inquiry,
            message.text,
            reference_year=reference_year,
            is_quote_relevant=self._is_quote_relevant(inquiry),
            tenant_id=message.tenant_id,
            provider=self._llm_provider,
        )

    def _system_state(self, message: InboundMessage) -> str:
        """"on" | "off" (tenant-wide schedule) | "paused_by_owner" (this one
        customer was manually paused via the handoff service, Layer 1).

        Checked in this order so an owner-paused customer is always recorded
        as "paused_by_owner", even while the tenant is also off -- otherwise
        the nightly digest / /待回覆 can't tell "owner already took over" from
        an ordinary off-mode silent drop and would re-surface it as noise."""
        if self._conversation_handoff_service is not None and self._conversation_handoff_service.is_paused(
            tenant_id=message.tenant_id,
            platform=message.platform,
            platform_user_id=message.platform_user_id,
        ):
            return "paused_by_owner"
        if not self._operation_mode_service.is_system_active(
            tenant_id=message.tenant_id,
            tenant_timezone=message.tenant_timezone,
        ):
            return "off"
        return "on"

    def _is_quote_relevant(self, inquiry: InquiryParseResult) -> bool:
        return (
            inquiry.intent.is_inquiry
            and inquiry.intent.inquiry_type in _QUOTE_RELEVANT_INTENTS
        )

    def _received_at_dt(self, message: InboundMessage) -> datetime:
        if message.timestamp is not None:
            return message.timestamp
        return self._now()

    def _received_at_iso(self, message: InboundMessage) -> str:
        return self._received_at_dt(message).isoformat()

    def _compute_is_night(self, message: InboundMessage) -> bool:
        local = self._received_at_dt(message).astimezone(
            ZoneInfo(message.tenant_timezone)
        )
        return local.hour >= _NIGHT_START_HOUR or local.hour < _NIGHT_END_HOUR

    def _build_base_log_payload(
        self,
        message: InboundMessage,
        *,
        system_state: str,
        action_taken: str,
    ) -> dict:
        payload = {
            "tenant_id": message.tenant_id,
            "received_at": self._received_at_iso(message),
            "platform": message.platform,
            "customer_platform_id": message.platform_user_id,
            "customer_display_name": message.customer_display_name,
            "raw_text": message.text,
            "system_state_at_time": system_state,
            "action_taken": action_taken,
            "is_night": self._compute_is_night(message),
        }
        payload.update(dict.fromkeys(_OPTIONAL_LOG_FIELDS, None))
        return payload

    def _add_parsed_fields_to_log(
        self,
        log: dict,
        inquiry: InquiryParseResult,
    ) -> None:
        log["inquiry_intent"] = inquiry.intent.inquiry_type
        log["parsed_checkin"] = inquiry.dates.checkin_date
        log["parsed_checkout"] = inquiry.dates.checkout_date
        log["parsed_adult_count"] = inquiry.guests.adult_count
        log["parsed_child_count"] = inquiry.guests.child_count
        log["parsed_infant_count"] = inquiry.guests.infant_count
        log["parsed_room_count"] = inquiry.room_count
        log["parsed_pet_count"] = inquiry.pets.pet_count
        # None (rather than a bare False) when this message never brought up
        # pets/BBQ at all, so the mapper into conversation_states slots can
        # tell "customer said no" from "customer didn't say anything" and
        # only clear existing state on the former (see
        # log_payload_to_state_slots).
        log["parsed_has_pet"] = inquiry.pets.has_pet if inquiry.pets.mentioned else None
        log["parsed_wants_bbq"] = inquiry.bbq.wants_bbq if inquiry.bbq.mentioned else None
        log["matched_faq_topics"] = list(inquiry.matched_faq_topics)
        log["llm_detected_intents"] = list(inquiry.llm_detected_intents)
        log["availability_probe_checkout"] = inquiry.availability_probe_checkout
        log["availability_probe_checkout_was_inferred"] = (
            inquiry.availability_probe_checkout_was_inferred
        )

    def _handle_urgent(
        self,
        message: InboundMessage,
        urgency: UrgencyDetectionResult,
    ) -> InquiryDecision:
        push_text = render_owner_push_urgent(
            original_text=message.text,
            matched_keywords=urgency.matched_keywords,
            display_name=message.customer_display_name,
        )
        # "unknown" is intentional: test invariant forbids calling is_system_active
        # on the urgent path (it has side effects). Future option: add a side-
        # effect-free peek method to OperationModeService if log fidelity matters.
        log = self._build_base_log_payload(
            message,
            system_state="unknown",
            action_taken="urgent",
        )
        log["urgency_category"] = urgency.category
        log["urgency_matched_keywords"] = list(urgency.matched_keywords)
        return InquiryDecision(
            action_type="push_owner_urgent",
            owner_push_text=push_text,
            was_urgent=True,
            log_payload=log,
        )

    def _handle_off_mode(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
        system_state: str = "off",
    ) -> InquiryDecision:
        log = self._build_base_log_payload(
            message,
            system_state=system_state,
            action_taken="off_mode_logged_only",
        )
        self._add_parsed_fields_to_log(log, inquiry)
        return InquiryDecision(
            action_type="do_nothing",
            was_system_off=True,
            log_payload=log,
            parsed_as_inquiry=inquiry.intent.is_inquiry,
        )

    def _handle_non_inquiry(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
    ) -> InquiryDecision:
        push_text = render_owner_push_uncategorized(
            original_text=message.text,
            display_name=message.customer_display_name,
            customer_was_replied=False,  # non-inquiry sends NO customer reply
        )
        log = self._build_base_log_payload(
            message,
            system_state="on",
            action_taken="non_inquiry_uncategorized",
        )
        self._add_parsed_fields_to_log(log, inquiry)
        return InquiryDecision(
            action_type="push_to_owner_only",
            owner_push_text=push_text,
            log_payload=log,
            parsed_as_inquiry=inquiry.intent.is_inquiry,
        )

    def _handle_missing_info(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
    ) -> InquiryDecision:
        reply_text = self._missing_info_reply(inquiry)
        log = self._build_base_log_payload(
            message,
            system_state="on",
            action_taken="missing_info",
        )
        self._add_parsed_fields_to_log(log, inquiry)
        log["missing_fields"] = list(inquiry.missing_fields)
        return InquiryDecision(
            action_type="reply_to_customer_only",
            customer_reply_text=reply_text,
            log_payload=log,
            parsed_as_inquiry=True,
        )

    def _missing_info_reply(self, inquiry: InquiryParseResult) -> str:
        if inquiry.needs_clarification and inquiry.clarification_reason == "date_range_too_broad":
            return render_date_range_clarification_message()
        return render_missing_info_message(
            missing_checkin="checkin_date" in inquiry.missing_fields,
            missing_checkout="checkout_date" in inquiry.missing_fields,
            missing_guest_count="guest_count" in inquiry.missing_fields,
            missing_pet_count="pet_count" in inquiry.missing_fields,
        )

    def _stay_kwargs(self, inquiry: InquiryParseResult) -> dict:
        return {
            "checkin_date": date.fromisoformat(inquiry.dates.checkin_date),
            "checkout_date": date.fromisoformat(inquiry.dates.checkout_date),
            "adult_count": inquiry.guests.adult_count,
            "child_count": inquiry.guests.child_count or 0,
            "infant_count": inquiry.guests.infant_count or 0,
            "pet_count": inquiry.pets.pet_count or 0,
            "wants_bbq": inquiry.bbq.wants_bbq,
            "room_count": inquiry.room_count,
        }

    def _handle_pricing(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
    ) -> InquiryDecision:
        room_policy = self._tenant_room_policy_loader(message.tenant_id)
        room_gate = self._room_gate(message, inquiry, room_policy)
        if room_gate is not None:
            return room_gate
        pricing = calculate_price(
            **self._stay_kwargs(inquiry),
            tenant_pricing=self._tenant_pricing_loader(message.tenant_id),
            room_policy=room_policy,
            tenant_special_dates=self._tenant_special_dates_loader(message.tenant_id),
        )
        if not pricing.can_quote:
            return self._handle_unquotable(message, inquiry, pricing)
        # Availability gate applies only to otherwise-quotable inquiries:
        # over-capacity and invalid-date are pricing-layer failures that
        # take precedence over calendar state.
        outcome = self._check_availability(inquiry)
        if outcome.status == "blocked":
            return self._handle_full_house(message, inquiry, outcome)
        if outcome.status == "error":
            return self._handle_quoted_unverified(message, inquiry, pricing, outcome)
        return self._handle_quoted(message, inquiry, pricing)

    def _room_gate(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
        room_policy: dict,
    ) -> InquiryDecision | None:
        guest_count = _guest_count(inquiry)
        room_count = inquiry.room_count
        if room_count is None:
            return self._handle_missing_room_count(message, inquiry)
        if _needs_manual_room_review(room_count, guest_count, room_policy):
            return self._handle_room_manual_review(message, inquiry)
        room_rule = resolve_room_pricing_rule(room_count=room_count, room_policy=room_policy)
        if guest_count <= room_rule.standard_capacity:
            return None
        if room_count == 4 and guest_count <= room_rule.max_capacity:
            return None
        return self._handle_room_capacity_suggestion(message, inquiry, room_policy)

    def _handle_missing_room_count(
        self, message: InboundMessage, inquiry: InquiryParseResult
    ) -> InquiryDecision:
        return self._room_reply(
            message,
            inquiry,
            "missing_room_count",
            render_missing_room_count_message(),
        )

    def _handle_room_capacity_suggestion(
        self, message: InboundMessage, inquiry: InquiryParseResult, room_policy: dict
    ) -> InquiryDecision:
        suggested = minimum_rooms_for_guest_count(
            guest_count=_guest_count(inquiry), room_policy=room_policy
        )
        if suggested is None:
            return self._handle_room_manual_review(message, inquiry)
        text = render_room_capacity_suggestion_message(
            guest_count=_guest_count(inquiry),
            room_count=inquiry.room_count,
            suggested_room_count=suggested,
        )
        return self._room_reply(message, inquiry, "room_capacity_suggestion", text)

    def _handle_room_manual_review(
        self, message: InboundMessage, inquiry: InquiryParseResult
    ) -> InquiryDecision:
        text = render_manual_review_message()
        log = self._room_log(message, inquiry, "room_manual_review")
        return InquiryDecision(
            action_type="reply_and_push",
            customer_reply_text=text,
            owner_push_text=self._manual_review_push(message),
            log_payload=log,
            parsed_as_inquiry=True,
            could_quote=False,
            completes_conversation_state=True,
        )

    def _room_reply(
        self, message: InboundMessage, inquiry: InquiryParseResult, action: str, text: str
    ) -> InquiryDecision:
        return InquiryDecision(
            action_type="reply_to_customer_only",
            customer_reply_text=text,
            log_payload=self._room_log(message, inquiry, action),
            parsed_as_inquiry=True,
            could_quote=False,
        )

    def _room_log(
        self, message: InboundMessage, inquiry: InquiryParseResult, action: str
    ) -> dict:
        log = self._build_base_log_payload(
            message, system_state="on", action_taken=action
        )
        self._add_parsed_fields_to_log(log, inquiry)
        return log

    def _manual_review_push(self, message: InboundMessage) -> str:
        return render_owner_push_uncategorized(
            original_text=message.text,
            display_name=message.customer_display_name,
            customer_was_replied=True,
        )

    def _check_availability(self, inquiry: InquiryParseResult) -> AvailabilityGateResult:
        return self._check_availability_range(
            checkin=date.fromisoformat(inquiry.dates.checkin_date),
            checkout=date.fromisoformat(inquiry.dates.checkout_date),
        )

    def _check_availability_range(
        self, *, checkin: date, checkout: date
    ) -> AvailabilityGateResult:
        return evaluate_availability_gate(
            availability_service=self._availability_service,
            checkin=checkin,
            checkout=checkout,
        )

    def _handle_probe_if_blocked(
        self, message: InboundMessage, inquiry: InquiryParseResult
    ) -> InquiryDecision | None:
        if not inquiry.availability_probe_checkout_was_inferred:
            return None
        checkin = date.fromisoformat(inquiry.dates.checkin_date)
        checkout = date.fromisoformat(inquiry.availability_probe_checkout)
        outcome = self._check_availability_range(checkin=checkin, checkout=checkout)
        if outcome.status != "blocked":
            return None
        return self._probe_full_house_decision(
            message, inquiry, outcome, checkin, checkout
        )

    def _probe_full_house_decision(
        self, message: InboundMessage, inquiry: InquiryParseResult,
        outcome: AvailabilityGateResult, checkin: date, checkout: date,
    ) -> InquiryDecision:
        log = self._probe_full_house_log(message, inquiry, outcome)
        reply = render_assumed_single_night_full_house_message(
            checkin_date=checkin, checkout_date=checkout,
        )
        push = render_owner_push_full_house(
            checkin_date=checkin, checkout_date=checkout, guest_count=_guest_count(inquiry),
            display_name=message.customer_display_name,
        )
        return InquiryDecision(
            action_type="reply_and_push",
            customer_reply_text=reply,
            owner_push_text=push,
            log_payload=log,
            parsed_as_inquiry=True,
            completes_conversation_state=True,
        )

    def _probe_full_house_log(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
        outcome: AvailabilityGateResult,
    ) -> dict:
        log = self._build_base_log_payload(
            message, system_state="on", action_taken="full_house"
        )
        self._add_parsed_fields_to_log(log, inquiry)
        log["blocked_nights_count"] = len(outcome.blocked_nights)
        log["missing_fields"] = list(inquiry.missing_fields)
        return log

    def _handle_unquotable(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
        pricing: PricingResult,
    ) -> InquiryDecision:
        reply_text, action_taken = self._unquotable_reply(pricing)
        log = self._build_base_log_payload(
            message,
            system_state="on",
            action_taken=action_taken,
        )
        self._add_parsed_fields_to_log(log, inquiry)
        return InquiryDecision(
            action_type="reply_to_customer_only",
            customer_reply_text=reply_text,
            log_payload=log,
            parsed_as_inquiry=True,
            could_quote=False,
            completes_conversation_state=True,
        )

    def _unquotable_reply(self, pricing: PricingResult) -> tuple[str, str]:
        if "exceeds_max_capacity" in pricing.reasons:
            return render_over_capacity_message(), "over_capacity"
        return render_invalid_date_message(), "invalid_date"

    def _handle_quoted(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
        pricing: PricingResult,
    ) -> InquiryDecision:
        reply_text = render_quote_message(pricing=pricing, **self._stay_kwargs(inquiry))
        log = self._build_base_log_payload(
            message,
            system_state="on",
            action_taken="quoted",
        )
        self._add_parsed_fields_to_log(log, inquiry)
        log["quoted_total"] = pricing.total
        return InquiryDecision(
            action_type="reply_to_customer_only",
            customer_reply_text=reply_text,
            log_payload=log,
            parsed_as_inquiry=True,
            could_quote=True,
            completes_conversation_state=True,
        )

    def _handle_full_house(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
        outcome: AvailabilityGateResult,
    ) -> InquiryDecision:
        push_text = self._render_full_house_push(message, inquiry)
        log = self._build_base_log_payload(
            message, system_state="on", action_taken="full_house"
        )
        self._add_parsed_fields_to_log(log, inquiry)
        log["blocked_nights_count"] = len(outcome.blocked_nights)
        return InquiryDecision(
            action_type="reply_and_push",
            customer_reply_text=render_full_house_message(),
            owner_push_text=push_text,
            log_payload=log,
            parsed_as_inquiry=True,
            completes_conversation_state=True,
        )

    def _render_full_house_push(
        self, message: InboundMessage, inquiry: InquiryParseResult
    ) -> str:
        return render_owner_push_full_house(
            checkin_date=date.fromisoformat(inquiry.dates.checkin_date),
            checkout_date=date.fromisoformat(inquiry.dates.checkout_date),
            guest_count=_guest_count(inquiry),
            display_name=message.customer_display_name,
        )

    def _handle_quoted_unverified(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
        pricing: PricingResult,
        outcome: AvailabilityGateResult,
    ) -> InquiryDecision:
        log = self._quoted_log_with_error(message, inquiry, pricing, outcome)
        return InquiryDecision(
            action_type="reply_and_push",
            customer_reply_text=render_quote_message(pricing=pricing, **self._stay_kwargs(inquiry)),
            owner_push_text=self._render_unverified_push(message, inquiry),
            log_payload=log,
            parsed_as_inquiry=True,
            could_quote=True,
            completes_conversation_state=True,
        )

    def _quoted_log_with_error(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
        pricing: PricingResult,
        outcome: AvailabilityGateResult,
    ) -> dict:
        log = self._build_base_log_payload(
            message, system_state="on", action_taken="quoted_unverified"
        )
        self._add_parsed_fields_to_log(log, inquiry)
        log["quoted_total"] = pricing.total
        log["availability_error_reason"] = outcome.reason
        return log

    def _render_unverified_push(
        self, message: InboundMessage, inquiry: InquiryParseResult
    ) -> str:
        return render_owner_push_availability_unverified(
            checkin_date=date.fromisoformat(inquiry.dates.checkin_date),
            checkout_date=date.fromisoformat(inquiry.dates.checkout_date),
            display_name=message.customer_display_name,
        )


def _guest_count(inquiry: InquiryParseResult) -> int:
    return (inquiry.guests.adult_count or 0) + (inquiry.guests.child_count or 0)


def _needs_manual_room_review(
    room_count: int, guest_count: int, room_policy: dict
) -> bool:
    capacity = max_guest_capacity(room_policy)
    if capacity is not None and guest_count > capacity:
        return True
    return (
        resolve_room_pricing_rule(room_count=room_count, room_policy=room_policy)
        is None
    )
