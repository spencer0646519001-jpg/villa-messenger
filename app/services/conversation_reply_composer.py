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

from datetime import date
from typing import Callable

from pydantic import BaseModel

from app.domain.faq_matcher import FaqMatch, NON_PRICEABLE, match_faq
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
    render_invalid_date_message,
    render_manual_review_message,
    render_missing_info_message,
    render_missing_room_count_message,
    render_over_capacity_message,
    render_owner_push_uncategorized,
    render_quote_message,
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


class ComposedReply(BaseModel):
    """What the route should do, described declaratively (the composer does NO
    I/O -- the route delivers).

      - send `text` (None = stay silent);
      - when `owner_push_text` is set, the route pushes it to the owner FIRST,
        and -- because "已通知" must be truthful -- swaps in `push_failed_text`
        as the customer reply if (and only if) that push fails;
      - when `completed_state_id` is set, best-effort mark that state completed.

    FAQ replies never set `completed_state_id` (FAQ does not touch quote state)."""

    text: str | None = None
    owner_push_text: str | None = None
    push_failed_text: str | None = None
    completed_state_id: int | None = None


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
    ) -> None:
        self._pricing_loader = tenant_pricing_loader
        self._special_dates_loader = tenant_special_dates_loader
        self._stay_policy_loader = tenant_stay_policy_loader
        self._amenities_loader = tenant_amenities_loader
        self._room_policy_loader = tenant_room_policy_loader
        self._location_loader = tenant_location_loader

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
        faq_match = match_faq(normalize_for_parsing(message.text))
        if faq_match is not None and faq_match.topic in NON_PRICEABLE:
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
            and _should_answer_gate3_faq(faq_match, decision, state)
        ):
            return self._compose_faq(message)
        if _is_faq(decision):
            return self._compose_faq(message)
        if state is None:
            if (
                decision.customer_reply_text is None
                and decision.owner_push_text is not None
                and faq_match is None
            ):
                return ComposedReply(owner_push_text=decision.owner_push_text)
            return ComposedReply(text=decision.customer_reply_text)
        missing = self._missing_for_state(state)
        if missing:
            return ComposedReply(text=_render_missing(missing))
        room_gate = self._room_gate(message, state)
        if room_gate is not None:
            return room_gate
        return ComposedReply(
            text=self._quote_for_state(message, state),
            completed_state_id=state["id"],
        )

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

    def _quote_for_state(self, message: InboundMessage, state: dict) -> str:
        kwargs = _state_stay_kwargs(state)
        room_policy = self._room_policy_loader(message.tenant_id)
        pricing = calculate_price(
            **kwargs,
            tenant_pricing=self._pricing_loader(message.tenant_id),
            room_policy=room_policy,
            tenant_special_dates=self._special_dates_loader(message.tenant_id),
        )
        if not pricing.can_quote:
            return _unquotable_reply(pricing)
        return render_quote_message(pricing=pricing, **kwargs)

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
            )
        room_rule = resolve_room_pricing_rule(room_count=room_count, room_policy=room_policy)
        if guest_count <= room_rule.standard_capacity:
            return None
        if room_count == 4 and guest_count <= room_rule.max_capacity:
            return None
        return _room_capacity_suggestion(message, room_count, guest_count, room_policy)


def _is_faq(decision: InquiryDecision) -> bool:
    """True when the per-message intent classifier said 'faq'.
    NON_PRICEABLE topic override (e.g. breakfast/pets while text also
    contains a price keyword) is handled at the call site in compose() —
    before this check — so this function only fires for non-NON_PRICEABLE faq."""
    return decision.log_payload.get("inquiry_intent") == "faq"


def _should_answer_gate3_faq(
    faq_match: FaqMatch, decision: InquiryDecision, state: dict | None
) -> bool:
    if faq_match.topic != "checkout":
        return decision.log_payload.get("inquiry_intent") != "price"
    return _is_bare_checkout_faq(decision, state)


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


def _needs_manual_room_review(
    room_count: int, guest_count: int, room_policy: dict
) -> bool:
    capacity = max_guest_capacity(room_policy)
    if capacity is not None and guest_count > capacity:
        return True
    return resolve_room_pricing_rule(room_count=room_count, room_policy=room_policy) is None


def _room_capacity_suggestion(
    message: InboundMessage, room_count: int, guest_count: int, room_policy: dict
) -> ComposedReply:
    suggested = minimum_rooms_for_guest_count(
        guest_count=guest_count,
        room_policy=room_policy,
    )
    if suggested is None:
        return ComposedReply(
            text=render_manual_review_message(),
            owner_push_text=_manual_review_push(message),
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
