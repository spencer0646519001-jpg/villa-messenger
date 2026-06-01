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
from app.clients.line_send_client import reply_message
from app.repositories.operation_state_repository import OperationStateRepository
from app.repositories.tenant_channel_repository import TenantChannelRepository
from app.repositories.tenant_repository import TenantRepository
from app.services.inquiry_service import InquiryService
from app.services.message_persistence_service import MessagePersistenceService
from app.services.operation_mode_service import OperationModeService
from app.services.tenant_config_loaders import (
    make_tenant_pricing_loader,
    make_tenant_special_dates_loader,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_GENERIC_400_DETAIL = "Bad Request"

# Stage 1 (proof-of-send): every inbound text event gets ONE hardcoded reply,
# unconditionally (no operation-mode gate -- intentional for the proof). Stage 2
# will replace this with the field-composed reply text from the decision.
_STAGE1_REPLY_TEXT = "收到您的訊息,測試回覆 ✅"
_ACCESS_TOKEN_ENV = "LINE_TEST_CHANNEL_ACCESS_TOKEN"


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


def _send_stage1_reply(event: dict) -> None:
    """Best-effort Stage 1 proof-of-send. Fully isolated from receiving: any
    failure (missing replyToken, missing token, LINE API error, network error,
    timeout) is logged at WARNING and swallowed so persistence is untouched and
    the webhook still returns 200."""
    reply_token = event.get("replyToken")
    if not reply_token:
        logger.warning("LINE reply skipped: event has no replyToken")
        return
    access_token = os.environ.get(_ACCESS_TOKEN_ENV)
    if not access_token:
        logger.warning("LINE reply skipped: %s not set", _ACCESS_TOKEN_ENV)
        return
    try:
        reply_message(reply_token=reply_token, text=_STAGE1_REPLY_TEXT, access_token=access_token)
    except Exception:  # noqa: BLE001 -- send must NEVER break receiving
        logger.warning("LINE reply send failed", exc_info=True)


def _run_pipeline(events: list[dict], tenant: dict, database_path: str) -> None:
    service = _build_inquiry_service(database_path)
    persistence = MessagePersistenceService(database_path=database_path)
    for event in events:
        message = line_event_to_inbound_message(
            event=event,
            tenant_id=tenant["id"],
            tenant_slug=tenant["slug"],
            tenant_timezone=tenant["timezone"],
        )
        persistence.persist(decision=service.handle_message(message=message))
        _send_stage1_reply(event)


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
