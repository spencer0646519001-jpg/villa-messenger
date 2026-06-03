"""
LINE webhook endpoint (receive-only, PR9b).

Receives a LINE webhook POST, verifies the HMAC signature over the RAW request
body, resolves the channel -> tenant, translates each text event into an
InboundMessage, runs it through the InquiryService pipeline, and PERSISTS the
resulting decision. It sends NO reply back to LINE -- that is PR10. We return
200 to acknowledge receipt and inspect the DB to confirm the system's judgment
before ever connecting a mouth.

Security posture (outward-opaque, inward-detailed):
  - The three rejection paths (malformed payload, unknown channel, bad
    signature) all raise an IDENTICAL generic 400 so an attacker cannot tell
    which check failed -- no channel enumeration, no signature probing.
  - The specific failure category + channel_id is logged server-side only.
    Secrets, tokens, and raw message text (PII) are NEVER logged.

The handler reads the raw body via `await request.body()` for the HMAC and only
then json.loads() it -- a Pydantic body param would re-serialize the bytes and
break signature verification.
"""

import json
import logging
import os
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.adapters.line_adapter import (
    LineParseError,
    extract_text_message_events,
    line_event_to_inbound_message,
)
from app.adapters.line_signature import LineSignatureError, verify_signature
from app.api.dependencies import get_database_path
from app.clients.line_send_client import push_message, reply_message
from app.domain.inquiry_decision import InquiryDecision
from app.repositories.conversation_state_repository import ConversationStateRepository
from app.repositories.operation_state_repository import OperationStateRepository
from app.repositories.tenant_channel_repository import TenantChannelRepository
from app.repositories.tenant_repository import TenantRepository
from app.schemas import InboundMessage
from app.services.conversation_reply_composer import (
    ComposedReply,
    ConversationReplyComposer,
)
from app.services.conversation_state_service import ConversationStateService
from app.services.inquiry_service import InquiryService
from app.services.message_persistence_service import MessagePersistenceService
from app.services.operation_mode_service import OperationModeService
from app.services.tenant_config_loaders import (
    make_tenant_amenities_loader,
    make_tenant_location_loader,
    make_tenant_pricing_loader,
    make_tenant_room_policy_loader,
    make_tenant_special_dates_loader,
    make_tenant_stay_policy_loader,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_GENERIC_400_DETAIL = "Bad Request"

# Stage 2: the reply text is the field-composed customer_reply_text the decision
# already carries. It is None in off mode / do_nothing / push-only decisions, in
# which case we send NOTHING (receive-only behavior is preserved).
_ACCESS_TOKEN_ENV = "LINE_TEST_CHANNEL_ACCESS_TOKEN"
# STAGE D: the owner's LINE userId, the target of confirm-and-defer FAQ pushes.
# Env-based for now (sandbox idiom); a TenantOwnerRepository + seeded
# tenant_owners row is the V2 follow-up.
_OWNER_USER_ID_ENV = "LINE_TEST_OWNER_USER_ID"


def _reject(category: str, channel_id: str | None) -> NoReturn:
    """Log the specific reason (server-side), raise an opaque generic 400."""
    logger.warning("LINE webhook rejected: %s (channel_id=%s)", category, channel_id)
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_GENERIC_400_DETAIL)


def _parse_payload(raw: bytes) -> dict:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        _reject("malformed payload: invalid JSON", None)
    if not isinstance(payload, dict):
        _reject("malformed payload: not a JSON object", None)
    return payload


def _resolve_channel(database_path: str, destination: str | None) -> dict:
    if not destination:
        _reject("malformed payload: missing destination", None)
    row = TenantChannelRepository(database_path).get_by_channel(platform="line", channel_id=destination)
    if row is None:
        _reject("unknown channel", destination)
    return row


def _verify(raw: bytes, signature: str | None, channel: dict) -> None:
    secret = os.environ.get(channel["channel_secret_ref"] or "")
    if not secret:
        _reject("channel secret env var not set", channel["channel_id"])
    try:
        verify_signature(request_body=raw, x_line_signature=signature, channel_secret=secret)
    except LineSignatureError:
        _reject("signature verification failed", channel["channel_id"])


def _resolve_tenant(database_path: str, channel: dict) -> dict:
    tenant = TenantRepository(database_path).get_by_id(channel["tenant_id"])
    if tenant is None:
        _reject("tenant row missing for channel", channel["channel_id"])
    return tenant


def _extract_events(payload: dict, channel: dict) -> list[dict]:
    try:
        return extract_text_message_events(payload)
    except LineParseError:
        _reject("malformed payload: events not a list", channel["channel_id"])


def _build_inquiry_service(database_path: str) -> InquiryService:
    operation_mode_service = OperationModeService(repo=OperationStateRepository(database_path))
    return InquiryService(
        operation_mode_service=operation_mode_service,
        tenant_pricing_loader=make_tenant_pricing_loader(database_path),
        tenant_special_dates_loader=make_tenant_special_dates_loader(database_path),
        availability_service=None,
    )


def _build_reply_composer(database_path: str) -> ConversationReplyComposer:
    return ConversationReplyComposer(
        tenant_pricing_loader=make_tenant_pricing_loader(database_path),
        tenant_special_dates_loader=make_tenant_special_dates_loader(database_path),
        tenant_stay_policy_loader=make_tenant_stay_policy_loader(database_path),
        tenant_amenities_loader=make_tenant_amenities_loader(database_path),
        tenant_room_policy_loader=make_tenant_room_policy_loader(database_path),
        tenant_location_loader=make_tenant_location_loader(database_path),
    )


def _event_to_message(event: dict, tenant: dict) -> InboundMessage:
    return line_event_to_inbound_message(
        event=event,
        tenant_id=tenant["id"],
        tenant_slug=tenant["slug"],
        tenant_timezone=tenant["timezone"],
    )


def _send_reply(event: dict, text: str | None) -> None:
    """Best-effort outbound reply. Fully isolated from receiving: any failure
    (no reply text, missing replyToken, missing token, LINE API error, network
    error, timeout) is logged at WARNING and swallowed so persistence is
    untouched and the webhook still returns 200."""
    if not text:
        return  # off mode / do_nothing / push-only -> send NOTHING
    reply_token = event.get("replyToken")
    if not reply_token:
        logger.warning("LINE reply skipped: event has no replyToken")
        return
    access_token = os.environ.get(_ACCESS_TOKEN_ENV)
    if not access_token:
        logger.warning("LINE reply skipped: %s not set", _ACCESS_TOKEN_ENV)
        return
    try:
        reply_message(reply_token=reply_token, text=text, access_token=access_token)
    except Exception:  # noqa: BLE001 -- send must NEVER break receiving
        logger.warning("LINE reply send failed", exc_info=True)


def _send_owner_push(text: str) -> bool:
    """Best-effort owner push (STAGE D). Returns True ONLY if LINE accepted the
    push -- the route uses that to keep "已通知" truthful. Same isolation as
    _send_reply: any failure (no owner id / no token / API / network) is logged
    and swallowed, never breaking the customer reply or the 200."""
    owner_user_id = os.environ.get(_OWNER_USER_ID_ENV)
    access_token = os.environ.get(_ACCESS_TOKEN_ENV)
    if not owner_user_id or not access_token:
        logger.warning("LINE owner push skipped: owner id or access token not set")
        return False
    try:
        push_message(to_user_id=owner_user_id, text=text, access_token=access_token)
        return True
    except Exception:  # noqa: BLE001 -- owner push must NEVER break the customer reply
        logger.warning("LINE owner push send failed", exc_info=True)
        return False


def _resolve_customer_text(composed: ComposedReply) -> str | None:
    """Push-first truthfulness seam: when the composer asked for an owner push,
    deliver it FIRST and only keep the "已通知" wording if it actually went
    out; on push failure fall back to the softer non-asserting wording. The
    composer stays I/O-free -- it DESCRIBES both variants, the route DELIVERS."""
    if composed.owner_push_text is None:
        return composed.text
    pushed = _send_owner_push(composed.owner_push_text)
    if not pushed and composed.push_failed_text is not None:
        return composed.push_failed_text
    return composed.text


def _record_state(
    state_service: ConversationStateService,
    message: InboundMessage,
    decision: InquiryDecision,
) -> dict | None:
    """Best-effort multi-turn state accumulation (STAGE B). Returns the merged
    in_progress state row (or None) so STAGE C can drive the reply from the
    ACCUMULATED slots. Fully isolated from receiving: any failure (DB error,
    unique-index race on the one-active constraint) is logged at WARNING and
    swallowed -- returning None falls back to the per-message reply -- so
    persistence/reply are untouched and the webhook still returns 200."""
    try:
        return state_service.record(message=message, decision=decision)
    except Exception:  # noqa: BLE001 -- state write must NEVER break receiving
        logger.warning("LINE conversation-state record failed", exc_info=True)
        return None


def _compose_reply(
    composer: ConversationReplyComposer,
    message: InboundMessage,
    decision: InquiryDecision,
    state: dict | None,
) -> ComposedReply:
    """Best-effort STAGE C reply composition. Any failure (e.g. a malformed
    accumulated state) is logged and falls back to the per-message reply, so a
    compose/quote error NEVER breaks receiving or the 200."""
    try:
        return composer.compose(message=message, decision=decision, state=state)
    except Exception:  # noqa: BLE001 -- compose must NEVER break receiving
        logger.warning("LINE state-reply compose failed", exc_info=True)
        return ComposedReply(text=decision.customer_reply_text)


def _mark_if_complete(
    state_service: ConversationStateService, composed: ComposedReply
) -> None:
    """Best-effort completion AFTER the reply is sent (send-first). A failure is
    logged and swallowed: the quote already went out, so at worst the still-open
    state re-quotes next turn (at-least-once), never a double-send this turn."""
    if composed.completed_state_id is None:
        return
    try:
        state_service.mark_completed(state_id=composed.completed_state_id)
    except Exception:  # noqa: BLE001 -- completion must NEVER break the sent reply
        logger.warning("LINE conversation-state mark_completed failed", exc_info=True)


def _run_pipeline(events: list[dict], tenant: dict, database_path: str) -> None:
    service = _build_inquiry_service(database_path)
    persistence = MessagePersistenceService(database_path=database_path)
    state_service = ConversationStateService(ConversationStateRepository(database_path))
    composer = _build_reply_composer(database_path)
    for event in events:
        message = _event_to_message(event, tenant)
        decision = service.handle_message(message=message)
        persistence.persist(decision=decision)
        state = _record_state(state_service, message, decision)
        composed = _compose_reply(composer, message, decision, state)
        customer_text = _resolve_customer_text(composed)
        _send_reply(event, customer_text)
        _mark_if_complete(state_service, composed)


@router.post("/webhooks/line")
async def line_webhook(request: Request, database_path: str = Depends(get_database_path)) -> dict[str, str]:
    raw = await request.body()
    signature = request.headers.get("X-Line-Signature")
    payload = _parse_payload(raw)
    channel = _resolve_channel(database_path, payload.get("destination"))
    _verify(raw, signature, channel)
    tenant = _resolve_tenant(database_path, channel)
    events = _extract_events(payload, channel)
    _run_pipeline(events, tenant, database_path)
    return {"status": "ok"}
