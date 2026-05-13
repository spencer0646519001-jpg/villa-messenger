"""
InquiryService — orchestrates one inbound customer message through the
V1.5 decision pipeline. Returns an InquiryDecision; the caller (PR8)
executes side effects (DB writes, LINE sends).

Allowed imports: stdlib, pydantic, app.domain.*, app.schemas.
Forbidden imports: app.repositories, app.api, app.adapters, app.clients,
app.services.* (other services).
"""

from datetime import date, datetime, timezone
from typing import Callable

from app.domain.inquiry_decision import InquiryDecision
from app.domain.inquiry_parser import parse_inquiry
from app.domain.parser_models import InquiryParseResult
from app.domain.pricing_models import PricingResult
from app.domain.pricing_policy import calculate_price
from app.domain.reply_templates import (
    render_invalid_date_message,
    render_missing_info_message,
    render_over_capacity_message,
    render_owner_push_uncategorized,
    render_owner_push_urgent,
    render_quote_message,
)
from app.domain.urgency_detector import UrgencyDetectionResult, detect_urgency
from app.schemas import InboundMessage


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
    "parsed_pet_count",
    "quoted_total",
    "missing_fields",
)


class InquiryService:
    def __init__(
        self,
        *,
        operation_mode_service,
        tenant_pricing_loader: Callable[[int], dict],
        tenant_special_dates_loader: Callable[[int], dict],
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._operation_mode_service = operation_mode_service
        self._tenant_pricing_loader = tenant_pricing_loader
        self._tenant_special_dates_loader = tenant_special_dates_loader
        self._now = now_provider or (lambda: datetime.now(timezone.utc))

    def handle_message(self, *, message: InboundMessage) -> InquiryDecision:
        urgency = detect_urgency(message.text)
        if urgency.is_urgent:
            return self._handle_urgent(message, urgency)
        inquiry = parse_inquiry(message.text)
        if not self._is_system_on(message):
            return self._handle_off_mode(message, inquiry)
        if not self._is_quote_relevant(inquiry):
            return self._handle_non_inquiry(message, inquiry)
        if inquiry.missing_fields:
            return self._handle_missing_info(message, inquiry)
        return self._handle_pricing(message, inquiry)

    def _is_system_on(self, message: InboundMessage) -> bool:
        return self._operation_mode_service.is_system_active(
            tenant_id=message.tenant_id,
            tenant_timezone=message.tenant_timezone,
        )

    def _is_quote_relevant(self, inquiry: InquiryParseResult) -> bool:
        return (
            inquiry.intent.is_inquiry
            and inquiry.intent.inquiry_type in _QUOTE_RELEVANT_INTENTS
        )

    def _received_at_iso(self, message: InboundMessage) -> str:
        if message.timestamp is not None:
            return message.timestamp.isoformat()
        return self._now().isoformat()

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
        log["parsed_pet_count"] = inquiry.pets.pet_count

    def _handle_urgent(
        self,
        message: InboundMessage,
        urgency: UrgencyDetectionResult,
    ) -> InquiryDecision:
        push_text = render_owner_push_urgent(
            original_text=message.text,
            matched_keywords=urgency.matched_keywords,
            contact_display=(message.customer_display_name or message.platform_user_id),
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
    ) -> InquiryDecision:
        log = self._build_base_log_payload(
            message,
            system_state="off",
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
        contact = message.customer_display_name or message.platform_user_id
        push_text = render_owner_push_uncategorized(
            original_text=message.text,
            contact_display=contact,
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
        reply_text = render_missing_info_message(
            missing_checkin="checkin_date" in inquiry.missing_fields,
            missing_checkout="checkout_date" in inquiry.missing_fields,
            missing_guest_count="guest_count" in inquiry.missing_fields,
            missing_pet_count="pet_count" in inquiry.missing_fields,
        )
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

    def _stay_kwargs(self, inquiry: InquiryParseResult) -> dict:
        return {
            "checkin_date": date.fromisoformat(inquiry.dates.checkin_date),
            "checkout_date": date.fromisoformat(inquiry.dates.checkout_date),
            "adult_count": inquiry.guests.adult_count,
            "child_count": inquiry.guests.child_count or 0,
            "infant_count": inquiry.guests.infant_count or 0,
            "pet_count": inquiry.pets.pet_count or 0,
        }

    def _handle_pricing(
        self,
        message: InboundMessage,
        inquiry: InquiryParseResult,
    ) -> InquiryDecision:
        pricing = calculate_price(
            **self._stay_kwargs(inquiry),
            tenant_pricing=self._tenant_pricing_loader(message.tenant_id),
            tenant_special_dates=self._tenant_special_dates_loader(message.tenant_id),
        )
        if not pricing.can_quote:
            return self._handle_unquotable(message, inquiry, pricing)
        return self._handle_quoted(message, inquiry, pricing)

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
        )
