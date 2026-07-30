"""
ConversationReplyComposer — STAGE C: turn the ACCUMULATED conversation state
into the customer reply, so a multi-turn flow that only completes across several
messages still produces a quote (the goldfish-memory payoff).

The per-message InquiryDecision answered one message in isolation. This composer
re-decides the reply from the merged state:
  - no active state            -> the per-message reply (today's behavior, intact)
  - off mode / urgent message  -> the per-message reply (stay silent / un-hijacked)
  - active state, still missing -> ask for the missing slot (accumulated missing)
  - active state, complete      -> quote from the accumulated slots, mark complete

It reuses the SAME domain functions as InquiryService — compute_missing_fields,
calculate_price, render_quote_message, render_missing_info_message — so the
quote/missing logic never diverges from the single-message path.

Allowed imports: stdlib, pydantic, app.domain.*, app.schemas. Like InquiryService
it is forbidden the repository layer; it receives the state row as a plain dict
and returns a ComposedReply for the route to send/act on.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Callable

from pydantic import BaseModel

from app.domain.availability_gate import (
    AvailabilityGateResult,
    AvailabilityServiceLike,
    evaluate_availability_gate,
)
from app.domain.faq_matcher import (
    FaqMatch,
    NON_PRICEABLE,
    is_booking_equivalent_topic,
    match_all_faq_topics,
    match_faq,
)
from app.domain.inquiry_completeness import compute_missing_fields
from app.domain.inquiry_decision import InquiryDecision
from app.domain.pricing_models import PricingResult
from app.domain.pricing_policy import calculate_price
from app.domain.reply_templates import (
    render_faq_amenities,
    render_faq_bbq,
    render_faq_breakfast,
    render_faq_checkout,
    render_faq_confirm_and_defer,
    render_faq_deposit,
    render_faq_location,
    render_faq_parking,
    render_faq_pets,
    render_faq_room_type,
    render_faq_wifi,
    render_faq_whole_house,
    render_full_house_message,
    render_invalid_date_message,
    render_manual_review_message,
    render_missing_info_message,
    render_missing_room_count_message,
    render_over_capacity_message,
    render_owner_push_availability_unverified,
    render_owner_push_full_house,
    render_owner_push_uncategorized,
    render_quote_message,
    render_reconfirm_stale_context_message,
    render_room_capacity_suggestion_message,
)
from app.domain.reply_text import (
    FAQ_FALLBACK_LEAD,
)
from app.domain.room_policy import (
    max_guest_capacity,
    minimum_rooms_for_guest_count,
    resolve_room_pricing_rule,
)
from app.domain.text_normalizer import normalize_for_parsing
from app.schemas import InboundMessage

# confirm-and-defer lead per tier-2 topic; non-whitelist faq uses the fallback.
# wifi and parking are now tier-1; no tier-2 topics remain.
_DEFER_LEADS: dict[str, str] = {}
_QUOTE_RELEVANT_INTENTS = {"price", "availability", "booking_question"}

# Layer 2 (23:00-boot-interrupt fix): a state whose slots were last touched
# while off/paused gets ONE reconfirmation nudge instead of an auto-quote once
# the bot is on again -- but only if enough time has passed that the customer
# plausibly forgot they asked. A same-session continuation within this window
# (e.g. 22:59 then 23:05) is common and harmless, so it is NOT nudged.
_STALE_RECONFIRM_MINUTES = 20


class ComposedReply(BaseModel):
    """What the route should do, described declaratively (the composer does NO
    I/O -- the route delivers).

      - send `text` (None = stay silent);
      - when `owner_push_text` is set, the route pushes it to the owner FIRST,
        and -- because "已通知" must be truthful -- swaps in `push_failed_text`
        as the customer reply if (and only if) that push fails;
      - when `completed_state_id` is set, best-effort mark that state completed.
        Quotes and manual-review handoffs both end the quote state.
      - when `reconfirm_shown_state_id` is set, best-effort clear that state's
        accumulated_while_off flag -- the nudge was shown once, so the state
        stays in_progress (unlike completed_state_id) but the NEXT turn
        proceeds normally instead of nudging again.

    FAQ replies never set `completed_state_id` (FAQ does not touch quote state)."""

    text: str | None = None
    owner_push_text: str | None = None
    push_failed_text: str | None = None
    completed_state_id: int | None = None
    reconfirm_shown_state_id: int | None = None


class ConversationReplyComposer:
    def __init__(
        self,
        *,
        tenant_pricing_loader: Callable[[int], dict],
        tenant_special_dates_loader: Callable[[int], dict],
        tenant_stay_policy_loader: Callable[[int], dict],
        tenant_amenities_loader: Callable[[int], dict],
        tenant_room_policy_loader: Callable[[int], dict],
        tenant_location_loader: Callable[[int], dict],
        availability_service: AvailabilityServiceLike | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._pricing_loader = tenant_pricing_loader
        self._special_dates_loader = tenant_special_dates_loader
        self._stay_policy_loader = tenant_stay_policy_loader
        self._amenities_loader = tenant_amenities_loader
        self._room_policy_loader = tenant_room_policy_loader
        self._location_loader = tenant_location_loader
        self._availability_service = availability_service
        self._now = now_provider or (lambda: datetime.now(timezone.utc))

    def compose(
        self,
        *,
        message: InboundMessage,
        decision: InquiryDecision,
        state: dict | None,
    ) -> ComposedReply:
        """Pick the reply. Order: urgent preserves owner push while sending no
        customer reply; off non-urgent stays silent; NON_PRICEABLE FAQ topics
        override price/availability intent (so '早餐多少錢嗎' answers breakfast,
        not quotes); remaining faq-intent fallback; then the state-driven
        quote/missing path."""
        if decision.was_urgent:
            return ComposedReply(
                text=decision.customer_reply_text,
                owner_push_text=decision.owner_push_text,
            )
        if decision.was_system_off:
            return ComposedReply(text=decision.customer_reply_text)
        faq_matches = match_all_faq_topics(normalize_for_parsing(message.text))
        faq_match = faq_matches[0] if faq_matches else None
        has_booking_equivalent_match = any(
            is_booking_equivalent_topic(match.topic) for match in faq_matches
        )
        if (
            faq_match is not None
            and faq_match.topic in NON_PRICEABLE
            and not _is_booking_equivalent_quote(
                decision, has_booking_equivalent_match
            )
        ):
            return self._compose_faq(message)
        # gate3: tier-1 FAQ match answers directly when it is clearly FAQ-only.
        # Excluded cases:
        #   checkout — "退房" collides with checkout-date slots ("5/14 退房 多少錢"
        #              must quote, not answer checkout time); only bare checkout FAQ
        #              with no parsed dates and no active quote state is safe.
        #   price intent — 決策F: a message that carries a price keyword ("多少錢",
        #              "費用", …) is asking for a quote; routing it to FAQ ignores the
        #              user's real request.  inquiry_intent read from log_payload, same
        #              as _is_faq, so the value is always present after the urgent/off
        #              early-returns above.
        if (
            faq_match is not None
            and faq_match.tier == 1
            and _should_answer_gate3_faq(
                faq_match,
                decision,
                state,
                has_booking_equivalent_match=has_booking_equivalent_match,
            )
        ):
            return self._compose_faq(message)
        if _is_faq(decision) and not _is_checkout_slot_followup(
            faq_match, decision, state
        ):
            return self._compose_faq(message)
        if state is None:
            if decision.completes_conversation_state:
                return ComposedReply(
                    text=decision.customer_reply_text,
                    owner_push_text=decision.owner_push_text,
                )
            if (
                decision.customer_reply_text is None
                and decision.owner_push_text is not None
                and faq_match is None
            ):
                return ComposedReply(owner_push_text=decision.owner_push_text)
            return ComposedReply(text=decision.customer_reply_text)
        if decision.completes_conversation_state:
            return ComposedReply(
                text=decision.customer_reply_text,
                owner_push_text=decision.owner_push_text,
                completed_state_id=state["id"],
            )
        if _is_stale_off_accumulation(state, self._now()):
            return ComposedReply(
                text=render_reconfirm_stale_context_message(),
                reconfirm_shown_state_id=state["id"],
            )
        early_gate = self._early_availability_gate(message, decision, state)
        if early_gate is not None:
            return early_gate
        missing = self._missing_for_state(state)
        if missing:
            return ComposedReply(text=_render_missing(missing))
        room_gate = self._room_gate(message, state)
        if room_gate is not None:
            return room_gate
        return self._quote_for_state(message, state)

    def _compose_faq(self, message: InboundMessage) -> ComposedReply:
        """A faq-intent message: tier-1 answers from config (no push); tier-2 and
        non-whitelist faq confirm-and-defer + a REAL owner push."""
        faq = match_faq(normalize_for_parsing(message.text))
        if faq is not None and faq.tier == 1:
            return ComposedReply(text=self._tier1_answer(message.tenant_id, faq.topic))
        lead = _defer_lead(faq)
        return ComposedReply(
            text=render_faq_confirm_and_defer(lead=lead, notified=True),
            push_failed_text=render_faq_confirm_and_defer(lead=lead, notified=False),
            owner_push_text=render_owner_push_uncategorized(
                original_text=message.text,
                display_name=message.customer_display_name,
                customer_was_replied=True,  # the confirm-and-defer reply DID go out
            ),
        )

    def _tier1_answer(self, tenant_id: int, topic: str) -> str:
        if topic == "breakfast":
            sp = self._stay_policy_loader(tenant_id)
            return render_faq_breakfast(breakfast_provided=bool(sp.get("breakfast_provided")))
        if topic == "checkout":
            sp = self._stay_policy_loader(tenant_id)
            return render_faq_checkout(
                check_in_after=sp.get("check_in_after"),
                checkout_before=sp.get("checkout_before"),
            )
        if topic == "pets":
            pets = self._pricing_loader(tenant_id).get("pets") or {}
            return render_faq_pets(
                allowed_with_notice=bool(pets.get("allowed_with_notice")),
                small_dogs_only=bool(pets.get("small_dogs_only_for_now")),
                fee_twd_per_pet=pets.get("fee_twd_per_pet_per_stay") or 0,
            )
        if topic == "wifi":
            sp = self._stay_policy_loader(tenant_id)
            return render_faq_wifi(
                provided=bool(sp.get("wifi_provided")),
                free=bool(sp.get("wifi_free")),
            )
        if topic == "parking":
            sp = self._stay_policy_loader(tenant_id)
            return render_faq_parking(
                available=bool(sp.get("parking_available")),
                free=bool(sp.get("parking_free")),
            )
        if topic == "whole_house":
            return render_faq_whole_house()
        if topic == "amenities":
            items = self._amenities_loader(tenant_id).get("items") or []
            return render_faq_amenities(items=items)
        if topic == "bbq":
            bbq = self._pricing_loader(tenant_id).get("bbq") or {}
            return render_faq_bbq(cleaning_fee_twd=bbq.get("cleaning_fee_twd") or 0)
        if topic == "deposit":
            deposits = self._pricing_loader(tenant_id).get("deposits") or {}
            return render_faq_deposit(
                equipment_security_deposit_on_arrival_twd=deposits.get(
                    "equipment_security_deposit_on_arrival_twd"
                ) or 0,
            )
        if topic == "room_type":
            description = self._room_policy_loader(tenant_id).get("description")
            return render_faq_room_type(description=description)
        if topic == "location":
            address = self._location_loader(tenant_id).get("address")
            return render_faq_location(address=address)
        raise ValueError(f"unhandled tier-1 FAQ topic: {topic!r}")

    def _missing_for_state(self, state: dict) -> list[str]:
        return compute_missing_fields(
            checkin_date=state["checkin_date"],
            checkout_date=state["checkout_date"],
            guest_count=_state_guest_count(state),
            has_pet=bool(state["has_pet"]),
            pet_count=state["pet_count"],
        )

    def _quote_for_state(self, message: InboundMessage, state: dict) -> ComposedReply:
        kwargs = _state_stay_kwargs(state)
        room_policy = self._room_policy_loader(message.tenant_id)
        pricing = calculate_price(
            **kwargs,
            tenant_pricing=self._pricing_loader(message.tenant_id),
            room_policy=room_policy,
            tenant_special_dates=self._special_dates_loader(message.tenant_id),
        )
        if not pricing.can_quote:
            return ComposedReply(
                text=_unquotable_reply(pricing),
                completed_state_id=state["id"],
            )
        gate = self._availability_gate(kwargs)
        if gate.status == "blocked":
            return self._blocked_availability_reply(message, state, kwargs)
        return self._quoted_reply(message, state, kwargs, pricing, gate)

    def _early_availability_gate(
        self, message: InboundMessage, decision: InquiryDecision, state: dict
    ) -> ComposedReply | None:
        if not _should_check_availability_early(state, decision):
            return None
        kwargs = _state_date_kwargs(state)
        gate = self._availability_gate(kwargs)
        if gate.status != "blocked":
            return None
        return self._blocked_availability_reply(message, state, kwargs)

    def _availability_gate(self, kwargs: dict) -> AvailabilityGateResult:
        return evaluate_availability_gate(
            availability_service=self._availability_service,
            checkin=kwargs["checkin_date"],
            checkout=kwargs["checkout_date"],
        )

    def _blocked_availability_reply(
        self, message: InboundMessage, state: dict, kwargs: dict
    ) -> ComposedReply:
        return ComposedReply(
            text=render_full_house_message(),
            owner_push_text=_full_house_push(message, kwargs, _state_guest_count(state)),
            completed_state_id=state["id"],
        )

    def _quoted_reply(
        self,
        message: InboundMessage,
        state: dict,
        kwargs: dict,
        pricing: PricingResult,
        gate: AvailabilityGateResult,
    ) -> ComposedReply:
        text = render_quote_message(pricing=pricing, **kwargs)
        if gate.status != "error":
            return ComposedReply(text=text, completed_state_id=state["id"])
        return ComposedReply(
            text=text,
            owner_push_text=_availability_unverified_push(message, kwargs),
            completed_state_id=state["id"],
        )

    def _room_gate(self, message: InboundMessage, state: dict) -> ComposedReply | None:
        room_policy = self._room_policy_loader(message.tenant_id)
        guest_count = _state_guest_count(state) or 0
        room_count = state.get("room_count")
        if room_count is None:
            return ComposedReply(text=render_missing_room_count_message())
        if _needs_manual_room_review(room_count, guest_count, room_policy):
            return ComposedReply(
                text=render_manual_review_message(),
                owner_push_text=_manual_review_push(message),
                completed_state_id=state["id"],
            )
        room_rule = resolve_room_pricing_rule(room_count=room_count, room_policy=room_policy)
        if guest_count <= room_rule.standard_capacity:
            return None
        if room_count == 4 and guest_count <= room_rule.max_capacity:
            return None
        return _room_capacity_suggestion(
            message, room_count, guest_count, room_policy, state_id=state["id"]
        )


def _is_stale_off_accumulation(state: dict, now: datetime) -> bool:
    if not state.get("accumulated_while_off"):
        return False
    last_touch = state.get("last_off_mode_update_at")
    if not last_touch:
        return False
    return now - datetime.fromisoformat(last_touch) > timedelta(minutes=_STALE_RECONFIRM_MINUTES)


def _is_faq(decision: InquiryDecision) -> bool:
    """True when the per-message intent classifier said 'faq'.
    NON_PRICEABLE topic override (e.g. breakfast/pets while text also
    contains a price keyword) is handled at the call site in compose() —
    before this check — so this function only fires for non-NON_PRICEABLE faq."""
    return decision.log_payload.get("inquiry_intent") == "faq"


def _is_checkout_slot_followup(
    faq_match: FaqMatch | None, decision: InquiryDecision, state: dict | None
) -> bool:
    return (
        state is not None
        and faq_match is not None
        and faq_match.topic == "checkout"
        and decision.log_payload.get("parsed_checkout") is not None
    )


def _should_answer_gate3_faq(
    faq_match: FaqMatch,
    decision: InquiryDecision,
    state: dict | None,
    *,
    has_booking_equivalent_match: bool | None = None,
) -> bool:
    product_match = (
        is_booking_equivalent_topic(faq_match.topic)
        if has_booking_equivalent_match is None
        else has_booking_equivalent_match
    )
    if _is_booking_equivalent_quote(decision, product_match):
        return False
    if faq_match.topic != "checkout":
        return decision.log_payload.get("inquiry_intent") != "price"
    return _is_bare_checkout_faq(decision, state)


def _is_booking_equivalent_quote(
    decision: InquiryDecision, has_booking_equivalent_match: bool
) -> bool:
    return (
        has_booking_equivalent_match
        and decision.log_payload.get("inquiry_intent") in _QUOTE_RELEVANT_INTENTS
    )


def _is_bare_checkout_faq(decision: InquiryDecision, state: dict | None) -> bool:
    return (
        decision.log_payload.get("inquiry_intent")
        not in ("price", "availability", "booking_question")
        and not decision.log_payload.get("parsed_checkin")
        and not decision.log_payload.get("parsed_checkout")
        and state is None
    )


def _defer_lead(faq: FaqMatch | None) -> str:
    """Tier-2 topic -> its lead; non-whitelist faq (faq is None) -> fallback."""
    if faq is None:
        return FAQ_FALLBACK_LEAD
    return _DEFER_LEADS[faq.topic]


def _state_guest_count(state: dict) -> int | None:
    """Derive guest_count from the stored adult/child slots (no guest_count
    column exists — total-guests-only messages stay unsupported, by design)."""
    return ((state["adult_count"] or 0) + (state["child_count"] or 0)) or None


def _state_stay_kwargs(state: dict) -> dict:
    """State slots -> calculate_price/render_quote_message kwargs (mirrors
    InquiryService._stay_kwargs: parse dates, default the optionals to 0)."""
    return {
        "checkin_date": date.fromisoformat(state["checkin_date"]),
        "checkout_date": date.fromisoformat(state["checkout_date"]),
        "adult_count": state["adult_count"],
        "child_count": state["child_count"] or 0,
        "infant_count": state["infant_count"] or 0,
        "pet_count": state["pet_count"] or 0,
        "room_count": state.get("room_count"),
    }


def _state_date_kwargs(state: dict) -> dict:
    return {
        "checkin_date": date.fromisoformat(state["checkin_date"]),
        "checkout_date": date.fromisoformat(state["checkout_date"]),
    }


def _should_check_availability_early(state: dict, decision: InquiryDecision) -> bool:
    if state.get("checkin_date") is None or state.get("checkout_date") is None:
        return False
    payload = decision.log_payload
    return payload.get("parsed_checkin") is not None or payload.get("parsed_checkout") is not None


def _needs_manual_room_review(
    room_count: int, guest_count: int, room_policy: dict
) -> bool:
    capacity = max_guest_capacity(room_policy)
    if capacity is not None and guest_count > capacity:
        return True
    return resolve_room_pricing_rule(room_count=room_count, room_policy=room_policy) is None


def _room_capacity_suggestion(
    message: InboundMessage,
    room_count: int,
    guest_count: int,
    room_policy: dict,
    *,
    state_id: int | None = None,
) -> ComposedReply:
    suggested = minimum_rooms_for_guest_count(
        guest_count=guest_count,
        room_policy=room_policy,
    )
    if suggested is None:
        return ComposedReply(
            text=render_manual_review_message(),
            owner_push_text=_manual_review_push(message),
            completed_state_id=state_id,
        )
    return ComposedReply(
        text=render_room_capacity_suggestion_message(
            guest_count=guest_count,
            room_count=room_count,
            suggested_room_count=suggested,
        )
    )


def _manual_review_push(message: InboundMessage) -> str:
    return render_owner_push_uncategorized(
        original_text=message.text,
        display_name=message.customer_display_name,
        customer_was_replied=True,
    )


def _availability_unverified_push(message: InboundMessage, kwargs: dict) -> str:
    return render_owner_push_availability_unverified(
        checkin_date=kwargs["checkin_date"],
        checkout_date=kwargs["checkout_date"],
        display_name=message.customer_display_name,
    )


def _full_house_push(
    message: InboundMessage, kwargs: dict, guest_count: int | None
) -> str:
    return render_owner_push_full_house(
        checkin_date=kwargs["checkin_date"],
        checkout_date=kwargs["checkout_date"],
        guest_count=guest_count,
        display_name=message.customer_display_name,
    )


def _render_missing(missing: list[str]) -> str:
    return render_missing_info_message(
        missing_checkin="checkin_date" in missing,
        missing_checkout="checkout_date" in missing,
        missing_guest_count="guest_count" in missing,
        missing_pet_count="pet_count" in missing,
    )


def _unquotable_reply(pricing: PricingResult) -> str:
    """Mirror InquiryService._unquotable_reply for a complete-but-unquotable
    accumulated state (e.g. over-capacity), so we answer instead of crashing."""
    if "exceeds_max_capacity" in pricing.reasons:
        return render_over_capacity_message()
    return render_invalid_date_message()
