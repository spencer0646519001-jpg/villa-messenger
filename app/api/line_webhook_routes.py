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
from time import sleep
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import NoReturn
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from app.adapters.line_adapter import (
    LineParseError,
    extract_text_message_events,
    line_event_to_inbound_message,
)
from app.adapters.llm import build_llm_provider_from_env
from app.adapters.line_signature import LineSignatureError, verify_signature
from app.api.dependencies import get_database_path
from app.clients.line_send_client import push_message, reply_message
from app.domain.inquiry_decision import InquiryDecision
from app.domain.operation_mode_resolver import compute_most_recent_schedule_window
from app.domain.reply_text import (
    OWNER_RECORD_EMPTY_HEADER,
    OWNER_RECORD_EMPTY_MESSAGE,
    OWNER_RECORD_GUEST_PREFIX,
    OWNER_RECORD_HEADER_TEMPLATE,
    OWNER_RECORD_SYSTEM_PREFIX,
    OWNER_RECORD_TRUNCATED_TEMPLATE,
    OWNER_RECORD_UNREPLIED_TEXT,
    OWNER_COMMAND_STATUS_OFF_MESSAGE,
    OWNER_COMMAND_STATUS_ON_MESSAGE,
    OWNER_COMMAND_TURN_OFF_MESSAGE,
    OWNER_COMMAND_TURN_ON_MESSAGE,
)
from app.domain.text_normalizer import normalize_for_parsing
from app.repositories.conversation_state_repository import ConversationStateRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.operation_state_repository import OperationStateRepository
from app.repositories.processed_webhook_event_repository import (
    ProcessedWebhookEventRepository,
)
from app.repositories.tenant_channel_repository import TenantChannelRepository
from app.repositories.tenant_owner_repository import TenantOwnerRepository
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
# STAGE D fallback: used only while a tenant has no active owner rows yet.
_OWNER_USER_ID_ENV = "LINE_TEST_OWNER_USER_ID"
_REPLY_RETRY_DELAYS_SECONDS = (0.5, 1.0)
_PIPELINE_FAILURE_OWNER_NOTICE = (
    "⚠️ 有一則客人訊息系統暫時無法處理，可能需要您手動查看 LINE 並回覆。"
)
_OWNER_COMMAND_TURN_ON = "/開機"
_OWNER_COMMAND_TURN_OFF = "/關機"
_OWNER_COMMAND_STATUS = "/狀態"
_OWNER_COMMAND_RECORD = "/紀錄"
_OWNER_COMMANDS = {
    _OWNER_COMMAND_TURN_ON,
    _OWNER_COMMAND_TURN_OFF,
    _OWNER_COMMAND_STATUS,
    _OWNER_COMMAND_RECORD,
}
_NIGHT_START_TIME = time(23, 0)
_NIGHT_END_TIME = time(8, 0)
_OWNER_RECORD_MAX_TEXT_CHARS = 4500


@dataclass(frozen=True)
class _PipelineContext:
    service: InquiryService
    persistence: MessagePersistenceService
    state_service: ConversationStateService
    composer: ConversationReplyComposer


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
        llm_provider=build_llm_provider_from_env(),
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


def _build_pipeline_context(database_path: str) -> _PipelineContext:
    return _PipelineContext(
        service=_build_inquiry_service(database_path),
        persistence=MessagePersistenceService(database_path=database_path),
        state_service=ConversationStateService(ConversationStateRepository(database_path)),
        composer=_build_reply_composer(database_path),
    )


def _event_to_message(event: dict, tenant: dict) -> InboundMessage:
    return line_event_to_inbound_message(
        event=event,
        tenant_id=tenant["id"],
        tenant_slug=tenant["slug"],
        tenant_timezone=tenant["timezone"],
    )


def _should_process_event(*, event: dict, tenant_id: int, database_path: str) -> bool:
    webhook_event_id = event.get("webhookEventId")
    if not webhook_event_id:
        logger.warning(
            "LINE webhook event missing webhookEventId; processing without dedupe "
            "(tenant_id=%s)",
            tenant_id,
        )
        return True
    is_new = ProcessedWebhookEventRepository(database_path).mark_if_new(
        tenant_id=tenant_id,
        webhook_event_id=str(webhook_event_id),
    )
    if not is_new:
        logger.info(
            "LINE webhook duplicate skipped: tenant_id=%s webhook_event_id=%s",
            tenant_id,
            webhook_event_id,
        )
    return is_new


def _reply_request(event: dict, text: str) -> tuple[str, str, str] | None:
    reply_token = event.get("replyToken")
    if not reply_token:
        logger.warning("LINE reply skipped: event has no replyToken")
        return None
    access_token = os.environ.get(_ACCESS_TOKEN_ENV)
    if not access_token:
        logger.warning("LINE reply skipped: %s not set", _ACCESS_TOKEN_ENV)
        return None
    return str(reply_token), text, access_token


def _send_reply_request(reply_token: str, text: str, access_token: str) -> bool:
    try:
        reply_message(reply_token=reply_token, text=text, access_token=access_token)
        return True
    except Exception:  # noqa: BLE001 -- send must NEVER break receiving
        logger.warning("LINE reply send failed", exc_info=True)
        return False


def _send_reply(event: dict, text: str | None) -> bool:
    """Best-effort single reply attempt; False means the expected reply was not sent."""
    if not text:
        return True  # off mode / do_nothing / push-only -> send NOTHING
    request = _reply_request(event, text)
    return request is not None and _send_reply_request(*request)


def _send_reply_with_retry(event: dict, text: str | None) -> bool:
    if not text:
        return True
    request = _reply_request(event, text)
    if request is None:
        return False
    if _send_reply_request(*request):
        return True
    for delay_seconds in _REPLY_RETRY_DELAYS_SECONDS:
        sleep(delay_seconds)
        if _send_reply_request(*request):
            return True
    return False


def _owner_user_ids(database_path: str, tenant_id: int) -> list[str]:
    owner_ids = TenantOwnerRepository(database_path).list_active_owner_user_ids(
        tenant_id=tenant_id,
        platform="line",
    )
    if owner_ids:
        return owner_ids
    fallback_owner_id = os.environ.get(_OWNER_USER_ID_ENV)
    if not fallback_owner_id:
        logger.warning("LINE owner push skipped: no active tenant owners configured")
        return []
    return [fallback_owner_id]


def _send_owner_push(*, database_path: str, tenant_id: int, text: str) -> bool:
    """Best-effort owner push; True iff at least one owner push succeeded."""
    access_token = os.environ.get(_ACCESS_TOKEN_ENV)
    if not access_token:
        logger.warning("LINE owner push skipped: %s not set", _ACCESS_TOKEN_ENV)
        return False
    sent_any = False
    for owner_user_id in _owner_user_ids(database_path, tenant_id):
        try:
            push_message(to_user_id=owner_user_id, text=text, access_token=access_token)
            sent_any = True
        except Exception:  # noqa: BLE001 -- keep trying other owners
            logger.warning("LINE owner push send failed", exc_info=True)
    return sent_any


def _parse_owner_command(text: str) -> str | None:
    command = normalize_for_parsing(text).strip()
    return command if command in _OWNER_COMMANDS else None


def _is_active_owner_sender(database_path: str, message: InboundMessage) -> bool:
    owner_ids = TenantOwnerRepository(database_path).list_active_owner_user_ids(
        tenant_id=message.tenant_id,
        platform="line",
    )
    return message.platform_user_id in owner_ids


def _push_owner_mode_change(
    *, command: str, message: InboundMessage, database_path: str, service: OperationModeService
) -> None:
    tenant_id = message.tenant_id
    timezone = message.tenant_timezone
    if command == _OWNER_COMMAND_TURN_ON:
        service.turn_on(tenant_id=tenant_id, tenant_timezone=timezone, by_owner_id=None)
        text = OWNER_COMMAND_TURN_ON_MESSAGE
    else:
        service.turn_off(tenant_id=tenant_id, tenant_timezone=timezone, by_owner_id=None)
        text = OWNER_COMMAND_TURN_OFF_MESSAGE
    _send_owner_push(database_path=database_path, tenant_id=tenant_id, text=text)


def _reply_owner_status(event: dict, message: InboundMessage, service: OperationModeService) -> None:
    tenant_id = message.tenant_id
    timezone = message.tenant_timezone
    active = service.is_system_active(tenant_id=tenant_id, tenant_timezone=timezone)
    text = OWNER_COMMAND_STATUS_ON_MESSAGE if active else OWNER_COMMAND_STATUS_OFF_MESSAGE
    _send_reply(event, text)


def _now_in_tenant_timezone(tenant_timezone: str) -> datetime:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(tenant_timezone))


def _owner_record_window_utc(message: InboundMessage) -> tuple[str, str]:
    now = _now_in_tenant_timezone(message.tenant_timezone)
    start, end = compute_most_recent_schedule_window(
        start_time=_NIGHT_START_TIME,
        end_time=_NIGHT_END_TIME,
        now=now,
    )
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def _owner_record_rows(*, database_path: str, message: InboundMessage) -> list[dict]:
    start, end = _owner_record_window_utc(message)
    rows = MessageRepository(database_path).list_between_created_at(
        tenant_id=message.tenant_id,
        start=start,
        end=end,
    )
    owner_ids = set(
        TenantOwnerRepository(database_path).list_active_owner_user_ids(
            tenant_id=message.tenant_id,
            platform="line",
        )
    )
    return [row for row in rows if row["platform_user_id"] not in owner_ids]


def _format_owner_record_entry(row: dict, tenant_zone: ZoneInfo) -> str:
    created_at = datetime.fromisoformat(row["created_at"]).astimezone(tenant_zone)
    reply_text = row["reply_text"] or OWNER_RECORD_UNREPLIED_TEXT
    return (
        f"{created_at:%m/%d %H:%M}\n"
        f"{OWNER_RECORD_GUEST_PREFIX}{row['message_text']}\n"
        f"{OWNER_RECORD_SYSTEM_PREFIX}{reply_text}"
    )


def _assemble_owner_record_text(header: str, entries: list[str], total: int) -> str:
    parts = [header, *entries]
    if len(entries) < total:
        parts.append(
            OWNER_RECORD_TRUNCATED_TEMPLATE.format(shown=len(entries), total=total)
        )
    return "\n\n".join(parts)


def _format_owner_record_reply(rows: list[dict], tenant_timezone: str) -> str:
    if not rows:
        return f"{OWNER_RECORD_EMPTY_HEADER}\n\n{OWNER_RECORD_EMPTY_MESSAGE}"
    tenant_zone = ZoneInfo(tenant_timezone)
    entries = [_format_owner_record_entry(row, tenant_zone) for row in rows]
    header = OWNER_RECORD_HEADER_TEMPLATE.format(count=len(entries))
    selected: list[str] = []
    for entry in reversed(entries):
        candidate = [entry, *selected]
        text = _assemble_owner_record_text(header, candidate, len(entries))
        if len(text) > _OWNER_RECORD_MAX_TEXT_CHARS and selected:
            break
        selected = candidate
        if len(text) > _OWNER_RECORD_MAX_TEXT_CHARS:
            break
    return _assemble_owner_record_text(header, selected, len(entries))


def _reply_owner_record(*, event: dict, message: InboundMessage, database_path: str) -> None:
    rows = _owner_record_rows(database_path=database_path, message=message)
    text = _format_owner_record_reply(rows, message.tenant_timezone)
    _send_reply(event, text)


def _handle_owner_command(*, event: dict, message: InboundMessage, database_path: str) -> bool:
    command = _parse_owner_command(message.text)
    if command is None or not _is_active_owner_sender(database_path, message):
        return False
    if command == _OWNER_COMMAND_RECORD:
        _reply_owner_record(event=event, message=message, database_path=database_path)
        return True
    service = OperationModeService(repo=OperationStateRepository(database_path))
    if command == _OWNER_COMMAND_STATUS:
        _reply_owner_status(event, message, service)
    else:
        _push_owner_mode_change(
            command=command, message=message, database_path=database_path, service=service
        )
    return True


def _resolve_customer_text(composed: ComposedReply, database_path: str, tenant_id: int) -> str | None:
    """Push first; use notified wording only when an owner push succeeded."""
    if composed.owner_push_text is None:
        return composed.text
    pushed = _send_owner_push(
        database_path=database_path,
        tenant_id=tenant_id,
        text=composed.owner_push_text,
    )
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
    state_service: ConversationStateService, tenant_id: int, composed: ComposedReply
) -> None:
    """Best-effort completion AFTER the reply is sent (send-first). A failure is
    logged and swallowed: the quote already went out, so at worst the still-open
    state re-quotes next turn (at-least-once), never a double-send this turn."""
    if composed.completed_state_id is None:
        return
    try:
        state_service.mark_completed(
            tenant_id=tenant_id,
            state_id=composed.completed_state_id,
        )
    except Exception:  # noqa: BLE001 -- completion must NEVER break the sent reply
        logger.warning("LINE conversation-state mark_completed failed", exc_info=True)


def _rollback_processed_event(*, event: dict, tenant_id: int, database_path: str) -> None:
    webhook_event_id = event.get("webhookEventId")
    if not webhook_event_id:
        logger.warning("LINE webhook event cannot roll back dedupe: missing webhookEventId")
        return
    ProcessedWebhookEventRepository(database_path).delete(
        tenant_id=tenant_id,
        webhook_event_id=str(webhook_event_id),
    )
    logger.warning(
        "LINE webhook dedupe rolled back for redelivery: tenant_id=%s webhook_event_id=%s",
        tenant_id,
        webhook_event_id,
    )


def _notify_owner_pipeline_failure(*, database_path: str, tenant_id: int) -> None:
    try:
        _send_owner_push(
            database_path=database_path,
            tenant_id=tenant_id,
            text=_PIPELINE_FAILURE_OWNER_NOTICE,
        )
    except Exception:  # noqa: BLE001 -- exception handling must never raise again
        logger.warning("LINE owner push for pipeline failure failed", exc_info=True)


def _handle_pipeline_failure(*, event: dict, tenant_id: int, database_path: str) -> None:
    logger.error(
        "LINE webhook pipeline failed: tenant_id=%s webhook_event_id=%s",
        tenant_id,
        event.get("webhookEventId"),
        exc_info=True,
    )
    _notify_owner_pipeline_failure(database_path=database_path, tenant_id=tenant_id)


def _process_pipeline_event(
    *,
    event: dict,
    tenant: dict,
    database_path: str,
    context: _PipelineContext,
) -> None:
    message = _event_to_message(event, tenant)
    if _handle_owner_command(event=event, message=message, database_path=database_path):
        return
    decision = context.service.handle_message(message=message)
    context.persistence.persist(decision=decision)
    state = _record_state(context.state_service, message, decision)
    composed = _compose_reply(context.composer, message, decision, state)
    customer_text = _resolve_customer_text(composed, database_path, message.tenant_id)
    if not _send_reply_with_retry(event, customer_text):
        _rollback_processed_event(
            event=event,
            tenant_id=message.tenant_id,
            database_path=database_path,
        )
        return
    _mark_if_complete(context.state_service, message.tenant_id, composed)


def _run_pipeline(events: list[dict], tenant: dict, database_path: str) -> None:
    context = _build_pipeline_context(database_path)
    tenant_id = tenant["id"]
    for event in events:
        if not _should_process_event(event=event, tenant_id=tenant_id, database_path=database_path):
            continue
        try:
            _process_pipeline_event(
                event=event,
                tenant=tenant,
                database_path=database_path,
                context=context,
            )
        except Exception:  # noqa: BLE001 -- keep the background task alive for later events
            _handle_pipeline_failure(event=event, tenant_id=tenant_id, database_path=database_path)


@router.post("/webhooks/line")
async def line_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    database_path: str = Depends(get_database_path),
) -> dict[str, str]:
    raw = await request.body()
    signature = request.headers.get("X-Line-Signature")
    payload = _parse_payload(raw)
    channel = _resolve_channel(database_path, payload.get("destination"))
    _verify(raw, signature, channel)
    tenant = _resolve_tenant(database_path, channel)
    events = _extract_events(payload, channel)
    # Starlette runs sync background callables in a threadpool after sending
    # the response. This is a same-process task, not a durable queue.
    background_tasks.add_task(_run_pipeline, events, tenant, database_path)
    return {"status": "ok"}
