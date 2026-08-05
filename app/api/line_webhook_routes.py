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
import re
from time import sleep
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
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
from app.clients.google_calendar_client import GoogleCalendarClient
from app.clients.line_send_client import get_profile, push_message, reply_message
from app.config_loader import (
    TenantConfigLoadError,
    load_google_calendar_settings,
    load_tenant_config,
)
from app.domain.inquiry_decision import InquiryDecision
from app.domain.operation_mode_resolver import compute_most_recent_schedule_window
from app.domain.reply_templates import (
    render_handoff_ambiguous_message,
    render_handoff_not_found_message,
    render_handoff_paused_message,
    render_handoff_resumed_message,
    render_owner_pending_digest_message,
)
from app.domain.reply_text import (
    OWNER_PENDING_EMPTY_HEADER,
    OWNER_PENDING_EMPTY_MESSAGE,
    OWNER_PENDING_HEADER_TEMPLATE,
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
from app.repositories.manual_hold_repository import ManualHoldRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.operation_state_repository import OperationStateRepository
from app.repositories.processed_webhook_event_repository import (
    ProcessedWebhookEventRepository,
)
from app.repositories.tenant_channel_repository import TenantChannelRepository
from app.repositories.tenant_owner_repository import TenantOwnerRepository
from app.repositories.tenant_repository import TenantRepository
from app.schemas import InboundMessage
from app.services.availability_service import AvailabilityService
from app.services.conversation_handoff_service import (
    ConversationHandoffService,
    DisplayNameLookupResult,
)
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
_CALENDAR_AVAILABILITY_ENABLED_ENV = "GOOGLE_CALENDAR_AVAILABILITY_ENABLED"
_REPLY_RETRY_DELAYS_SECONDS = (0.5, 1.0)
_PIPELINE_FAILURE_OWNER_NOTICE = (
    "⚠️ 有一則客人訊息系統暫時無法處理，可能需要您手動查看 LINE 並回覆。"
)
_OWNER_COMMAND_TURN_ON = "/開機"
_OWNER_COMMAND_TURN_OFF = "/關機"
_OWNER_COMMAND_STATUS = "/狀態"
_OWNER_COMMAND_RECORD = "/紀錄"
_OWNER_COMMAND_PENDING = "/待回覆"
_OWNER_COMMANDS = {
    _OWNER_COMMAND_TURN_ON,
    _OWNER_COMMAND_TURN_OFF,
    _OWNER_COMMAND_STATUS,
    _OWNER_COMMAND_RECORD,
    _OWNER_COMMAND_PENDING,
}
_NIGHT_START_TIME = time(23, 0)
_NIGHT_END_TIME = time(8, 0)
_OWNER_RECORD_MAX_TEXT_CHARS = 4500
_DIGEST_UNHANDLED_LIMIT = 50


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


def _build_handoff_service(database_path: str) -> ConversationHandoffService:
    return ConversationHandoffService(
        hold_repo=ManualHoldRepository(database_path),
        message_repo=MessageRepository(database_path),
        operation_state_repo=OperationStateRepository(database_path),
    )


def _build_inquiry_service(
    database_path: str,
    availability_service: AvailabilityService | None = None,
) -> InquiryService:
    operation_mode_service = OperationModeService(repo=OperationStateRepository(database_path))
    return InquiryService(
        operation_mode_service=operation_mode_service,
        conversation_handoff_service=_build_handoff_service(database_path),
        tenant_pricing_loader=make_tenant_pricing_loader(database_path),
        tenant_special_dates_loader=make_tenant_special_dates_loader(database_path),
        tenant_room_policy_loader=make_tenant_room_policy_loader(database_path),
        availability_service=availability_service,
        llm_provider=build_llm_provider_from_env(),
    )


def _build_reply_composer(
    database_path: str,
    availability_service: AvailabilityService | None = None,
) -> ConversationReplyComposer:
    return ConversationReplyComposer(
        tenant_pricing_loader=make_tenant_pricing_loader(database_path),
        tenant_special_dates_loader=make_tenant_special_dates_loader(database_path),
        tenant_stay_policy_loader=make_tenant_stay_policy_loader(database_path),
        tenant_amenities_loader=make_tenant_amenities_loader(database_path),
        tenant_room_policy_loader=make_tenant_room_policy_loader(database_path),
        tenant_location_loader=make_tenant_location_loader(database_path),
        availability_service=availability_service,
    )


def _build_pipeline_context(database_path: str, tenant: dict) -> _PipelineContext:
    availability_service = _build_availability_service(tenant)
    return _PipelineContext(
        service=_build_inquiry_service(database_path, availability_service),
        persistence=MessagePersistenceService(database_path=database_path),
        state_service=ConversationStateService(ConversationStateRepository(database_path)),
        composer=_build_reply_composer(database_path, availability_service),
    )


def _build_availability_service(tenant: dict) -> AvailabilityService | None:
    if not _env_bool(_CALENDAR_AVAILABILITY_ENABLED_ENV, default=False):
        return None
    settings = _load_google_calendar_settings_for_tenant(tenant)
    if settings is None:
        return None
    client = GoogleCalendarClient(
        credentials_path=settings["credentials_path"],
        calendar_id=settings["calendar_id"],
    )
    return AvailabilityService(
        client=client,
        booking_keywords=settings["booking_keywords"],
        enabled=True,
    )


def _load_google_calendar_settings_for_tenant(tenant: dict) -> dict | None:
    try:
        config = load_tenant_config(tenant_slug=tenant["slug"])
        return load_google_calendar_settings(config)
    except TenantConfigLoadError:
        logger.warning("Google Calendar availability disabled: config load failed", exc_info=True)
        return None


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _fetch_customer_display_name(platform_user_id: str) -> str | None:
    """Best-effort LINE profile lookup so owner pushes / the "/<name>" handoff
    command have a real name to work with. Never raises: a lookup failure
    (blocked OA, transient LINE API error) must never break receiving --
    the message just keeps flowing with no display name, exactly like
    before this lookup existed."""
    access_token = os.environ.get(_ACCESS_TOKEN_ENV)
    if not access_token:
        return None
    try:
        profile = get_profile(user_id=platform_user_id, access_token=access_token)
    except Exception:  # noqa: BLE001 -- profile lookup must NEVER break receiving
        logger.warning("LINE profile fetch failed", exc_info=True)
        return None
    return profile.get("displayName")


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


def _parse_fixed_owner_command(text: str) -> str | None:
    command = normalize_for_parsing(text).strip()
    return command if command in _OWNER_COMMANDS else None


_AMBIGUOUS_CANDIDATE_SUFFIX = re.compile(r"^(.*\S)\s+(\d+)$")


def _parse_display_name_command(text: str) -> tuple[str, str, int | None] | None:
    """A '/<name>' that is not one of the fixed commands is a pause/resume
    toggle target for the named customer (Layer 1 handoff). Returns
    (raw_name, split_name, candidate_index) with the leading '/' stripped, or
    None when the text is not that shape. A trailing "<space><digits>" (e.g.
    "/Wendy 1") is the owner picking a numbered candidate from a prior
    ambiguous-name reply, so it is ALSO split off as candidate_index --
    raw_name keeps the full string (untouched) so the caller can try it as a
    literal display name first, since a real display name can itself end in
    digits (e.g. "Room 101"), which split_name/candidate_index alone cannot
    represent. Normalizes first (same as _parse_fixed_owner_command) so a
    full-width '／' slash still matches, like the existing fixed commands."""
    stripped = normalize_for_parsing(text).strip()
    if not stripped.startswith("/"):
        return None
    name = stripped[1:].strip()
    if not name:
        return None
    match = _AMBIGUOUS_CANDIDATE_SUFFIX.match(name)
    if match:
        return name, match.group(1), int(match.group(2))
    return name, name, None


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


def _truncate_rows_to_char_limit(
    rows: list[dict], entries: list[str], header: str
) -> tuple[list[str], list[dict]]:
    """Keep as many of the MOST RECENT entries (from the end of the list) as
    fit within _OWNER_RECORD_MAX_TEXT_CHARS, dropping older ones first. rows
    and entries must be index-aligned; returns the kept entries alongside
    their source rows so a caller can tell exactly what was shown."""
    selected_entries: list[str] = []
    selected_rows: list[dict] = []
    for row, entry in zip(reversed(rows), reversed(entries)):
        candidate_entries = [entry, *selected_entries]
        text = _assemble_owner_record_text(header, candidate_entries, len(entries))
        if len(text) > _OWNER_RECORD_MAX_TEXT_CHARS and selected_entries:
            break
        selected_entries = candidate_entries
        selected_rows = [row, *selected_rows]
        if len(text) > _OWNER_RECORD_MAX_TEXT_CHARS:
            break
    return selected_entries, selected_rows


def _format_owner_record_reply(rows: list[dict], tenant_timezone: str) -> str:
    if not rows:
        return f"{OWNER_RECORD_EMPTY_HEADER}\n\n{OWNER_RECORD_EMPTY_MESSAGE}"
    tenant_zone = ZoneInfo(tenant_timezone)
    entries = [_format_owner_record_entry(row, tenant_zone) for row in rows]
    header = OWNER_RECORD_HEADER_TEMPLATE.format(count=len(entries))
    selected, _ = _truncate_rows_to_char_limit(rows, entries, header)
    return _assemble_owner_record_text(header, selected, len(entries))


def _reply_owner_record(*, event: dict, message: InboundMessage, database_path: str) -> None:
    rows = _owner_record_rows(database_path=database_path, message=message)
    text = _format_owner_record_reply(rows, message.tenant_timezone)
    _send_reply(event, text)


def _pending_rows(*, database_path: str, tenant_id: int) -> list[dict]:
    """messages that never got a customer reply nor an owner push (handled=0).
    list_unhandled already excludes ones the owner took ownership of via a
    handoff pause at the SQL level -- those would just be noise she already
    knows about."""
    return MessageRepository(database_path).list_unhandled(tenant_id, limit=_DIGEST_UNHANDLED_LIMIT)


def _close_pending_rows(*, database_path: str, tenant_id: int, rows: list[dict]) -> None:
    """Once backlog rows have actually been shown to the owner (via /待回覆
    or the nightly digest), mark them handled so they are not reported again
    tomorrow -- otherwise the backlog only ever grows."""
    try:
        MessageRepository(database_path).mark_many_handled(
            tenant_id=tenant_id, message_ids=[row["id"] for row in rows]
        )
    except Exception:  # noqa: BLE001 -- closing the backlog must NEVER break the reply already sent
        logger.warning("LINE pending-backlog close failed", exc_info=True)


def _format_owner_pending_reply(
    rows: list[dict], tenant_timezone: str
) -> tuple[str, list[dict]]:
    """Returns (reply_text, shown_rows) -- shown_rows is the (possibly
    truncated) subset of `rows` actually included in reply_text, so the
    caller only closes out backlog entries the owner was actually shown."""
    if not rows:
        return f"{OWNER_PENDING_EMPTY_HEADER}\n\n{OWNER_PENDING_EMPTY_MESSAGE}", []
    tenant_zone = ZoneInfo(tenant_timezone)
    entries = [_format_owner_record_entry(row, tenant_zone) for row in rows]
    header = OWNER_PENDING_HEADER_TEMPLATE.format(count=len(entries))
    selected_entries, selected_rows = _truncate_rows_to_char_limit(rows, entries, header)
    return _assemble_owner_record_text(header, selected_entries, len(entries)), selected_rows


def _reply_owner_pending(*, event: dict, message: InboundMessage, database_path: str) -> None:
    rows = _pending_rows(database_path=database_path, tenant_id=message.tenant_id)
    text, shown_rows = _format_owner_pending_reply(rows, message.tenant_timezone)
    if _send_reply(event, text) and shown_rows:
        _close_pending_rows(
            database_path=database_path, tenant_id=message.tenant_id, rows=shown_rows
        )


def _select_ambiguous_candidate(
    lookup: DisplayNameLookupResult, candidate_index: int | None
) -> str | None:
    """None means still ambiguous -- caller must (re-)show the candidate
    list rather than guess."""
    if candidate_index is None:
        return None
    if 1 <= candidate_index <= len(lookup.candidates):
        return lookup.candidates[candidate_index - 1].platform_user_id
    return None


def _send_ambiguous_reply(
    *, event: dict, message: InboundMessage, display_name: str, candidates: list
) -> None:
    tenant_zone = ZoneInfo(message.tenant_timezone)
    local_times = [
        f"{datetime.fromisoformat(c.last_message_at).astimezone(tenant_zone):%m/%d %H:%M}"
        for c in candidates
    ]
    _send_reply(
        event,
        render_handoff_ambiguous_message(
            display_name=display_name, candidates_local_times=local_times
        ),
    )


def _reply_owner_handoff_toggle(
    *,
    event: dict,
    message: InboundMessage,
    database_path: str,
    raw_name: str,
    display_name: str,
    candidate_index: int | None,
) -> None:
    handoff_service = _build_handoff_service(database_path)
    if candidate_index is not None:
        # The text had a trailing "<space><digits>" shape (e.g. "/Room 101"),
        # which is ambiguous between "candidate #101 of a prior ambiguous
        # list" and "a literal display name that itself ends in digits".
        # Try the untouched raw string as an exact display name FIRST -- only
        # fall back to treating the suffix as a candidate index when no
        # customer is actually named that.
        exact_lookup = handoff_service.resolve_by_display_name(
            tenant_id=message.tenant_id, platform=message.platform, display_name=raw_name
        )
        if exact_lookup.status == "found":
            display_name = raw_name
            candidate_index = None
            lookup = exact_lookup
        else:
            lookup = handoff_service.resolve_by_display_name(
                tenant_id=message.tenant_id, platform=message.platform, display_name=display_name
            )
    else:
        lookup = handoff_service.resolve_by_display_name(
            tenant_id=message.tenant_id, platform=message.platform, display_name=display_name
        )
    if lookup.status == "not_found":
        _send_reply(event, render_handoff_not_found_message(display_name=display_name))
        return
    platform_user_id = (
        lookup.platform_user_id
        if lookup.status == "found"
        else _select_ambiguous_candidate(lookup, candidate_index)
    )
    if platform_user_id is None:
        _send_ambiguous_reply(
            event=event, message=message, display_name=display_name, candidates=lookup.candidates
        )
        return
    action = handoff_service.toggle(
        tenant_id=message.tenant_id,
        tenant_timezone=message.tenant_timezone,
        platform=message.platform,
        platform_user_id=platform_user_id,
        owner_id=None,
    )
    text = (
        render_handoff_paused_message(display_name=display_name)
        if action == "paused"
        else render_handoff_resumed_message(display_name=display_name)
    )
    _send_reply(event, text)


def _handle_owner_command(*, event: dict, message: InboundMessage, database_path: str) -> bool:
    if not normalize_for_parsing(message.text).strip().startswith("/"):
        return False
    if not _is_active_owner_sender(database_path, message):
        return False
    command = _parse_fixed_owner_command(message.text)
    if command == _OWNER_COMMAND_RECORD:
        _reply_owner_record(event=event, message=message, database_path=database_path)
        return True
    if command == _OWNER_COMMAND_PENDING:
        _reply_owner_pending(event=event, message=message, database_path=database_path)
        return True
    if command is not None:
        service = OperationModeService(repo=OperationStateRepository(database_path))
        if command == _OWNER_COMMAND_STATUS:
            _reply_owner_status(event, message, service)
        else:
            _push_owner_mode_change(
                command=command, message=message, database_path=database_path, service=service
            )
        return True
    parsed_display_name = _parse_display_name_command(message.text)
    if parsed_display_name:
        raw_name, display_name, candidate_index = parsed_display_name
        _reply_owner_handoff_toggle(
            event=event,
            message=message,
            database_path=database_path,
            raw_name=raw_name,
            display_name=display_name,
            candidate_index=candidate_index,
        )
    return True


def _resolve_customer_text(
    composed: ComposedReply, database_path: str, tenant_id: int
) -> tuple[str | None, bool]:
    """Push first; use notified wording only when an owner push succeeded.
    Returns (customer_text, owner_push_succeeded) -- the second element is
    False whenever no push was attempted."""
    if composed.owner_push_text is None:
        return composed.text, False
    pushed = _send_owner_push(
        database_path=database_path,
        tenant_id=tenant_id,
        text=composed.owner_push_text,
    )
    if not pushed and composed.push_failed_text is not None:
        return composed.push_failed_text, pushed
    return composed.text, pushed


def _correct_handled_if_silent_drop(
    *,
    database_path: str,
    tenant_id: int,
    message_id: int,
    decision: InquiryDecision,
    reached_someone: bool,
) -> None:
    """decision_to_db_mapper optimistically marks handled=True for every
    action_type other than "do_nothing" -- correct here when the actual
    delivery attempt (customer reply and/or owner push) failed and nobody
    was actually informed, so the row reappears in /待回覆 and the nightly
    digest instead of being silently lost."""
    if reached_someone or decision.action_type == "do_nothing":
        return
    try:
        MessageRepository(database_path).mark_unhandled(
            tenant_id=tenant_id, message_id=message_id
        )
    except Exception:  # noqa: BLE001 -- correction must NEVER break receiving
        logger.warning("LINE handled-flag correction failed", exc_info=True)


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


def _clear_reconfirm_if_shown(
    state_service: ConversationStateService, tenant_id: int, composed: ComposedReply
) -> None:
    """Best-effort AFTER the reply is sent (send-first), mirrors _mark_if_complete.
    The Layer 2 reconfirmation nudge was just sent -- clear accumulated_while_off
    so the NEXT turn proceeds normally instead of nudging again."""
    if composed.reconfirm_shown_state_id is None:
        return
    try:
        state_service.clear_accumulated_while_off(
            tenant_id=tenant_id,
            state_id=composed.reconfirm_shown_state_id,
        )
    except Exception:  # noqa: BLE001 -- clearing must NEVER break the sent reply
        logger.warning("LINE conversation-state clear_accumulated_while_off failed", exc_info=True)


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
    display_name = _fetch_customer_display_name(message.platform_user_id)
    if display_name:
        message = message.model_copy(update={"customer_display_name": display_name})
    decision = context.service.handle_message(message=message)
    persisted = context.persistence.persist(decision=decision)
    message_id = persisted["message_id"]
    state = _record_state(context.state_service, message, decision)
    composed = _compose_reply(context.composer, message, decision, state)
    customer_text, owner_push_ok = _resolve_customer_text(
        composed, database_path, message.tenant_id
    )
    if not _send_reply_with_retry(event, customer_text):
        _correct_handled_if_silent_drop(
            database_path=database_path,
            tenant_id=message.tenant_id,
            message_id=message_id,
            decision=decision,
            reached_someone=owner_push_ok,
        )
        _rollback_processed_event(
            event=event,
            tenant_id=message.tenant_id,
            database_path=database_path,
        )
        return
    _correct_handled_if_silent_drop(
        database_path=database_path,
        tenant_id=message.tenant_id,
        message_id=message_id,
        decision=decision,
        reached_someone=customer_text is not None or owner_push_ok,
    )
    _mark_if_complete(context.state_service, message.tenant_id, composed)
    _clear_reconfirm_if_shown(context.state_service, message.tenant_id, composed)


def _run_pipeline(events: list[dict], tenant: dict, database_path: str) -> None:
    context = _build_pipeline_context(database_path, tenant)
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


def _digest_window_start_date(
    *, start_time: time, end_time: time, now_local: datetime
) -> str | None:
    """Tenant-local calendar date the current auto-on window began, or None
    if now is outside the window. start_time may wrap past midnight (e.g.
    23:00-08:00) -- during the 00:00-end_time tail the window "began
    yesterday" even though now_local's own date has already rolled over, so
    a bare now_local.date() would misidentify the boundary (and cause a
    restart during that tail to think the digest already happened, or won't
    happen until tomorrow -- see the 27bc622 case study)."""
    now_time = now_local.time()
    if start_time <= end_time:
        if not (start_time <= now_time < end_time):
            return None
        return now_local.date().isoformat()
    if now_time >= start_time:
        return now_local.date().isoformat()
    if now_time < end_time:
        return (now_local.date() - timedelta(days=1)).isoformat()
    return None


def _check_and_send_digest_for_tenant(
    *, tenant: dict, database_path: str, state_repo: OperationStateRepository
) -> None:
    tenant_id = tenant["id"]
    state = state_repo.get_or_create(tenant_id)
    if not state["auto_schedule_enabled"]:
        return
    now_local = _now_in_tenant_timezone(tenant["timezone"])
    window_start_date = _digest_window_start_date(
        start_time=time.fromisoformat(state["auto_on_start_time"]),
        end_time=time.fromisoformat(state["auto_on_end_time"]),
        now_local=now_local,
    )
    if window_start_date is None or state.get("last_digest_sent_date") == window_start_date:
        return
    rows = _pending_rows(database_path=database_path, tenant_id=tenant_id)
    if not rows:
        state_repo.mark_digest_sent(tenant_id=tenant_id, date_str=window_start_date)
        return
    text = render_owner_pending_digest_message(count=len(rows))
    # Mark sent only AFTER a confirmed push: an unconfirmed/failed push must
    # stay retryable on the next 5-minute poll tick instead of being silently
    # given up on for the rest of the day.
    if not _send_owner_push(database_path=database_path, tenant_id=tenant_id, text=text):
        return
    state_repo.mark_digest_sent(tenant_id=tenant_id, date_str=window_start_date)
    # Do NOT close the rows here: the digest only pushes a COUNT ("共 N 則,
    # 請輸入 /待回覆 查看"), never the actual message content. Closing them now
    # would make them vanish before the owner ever sees them via /待回覆 --
    # only _reply_owner_pending (which actually shows the content) may close
    # backlog rows.


def run_nightly_digest_check(database_path: str) -> None:
    """Best-effort: for each active tenant, once per tenant-local day after
    auto_on_start_time, push ONE batched digest of messages that arrived
    while the system was off/paused and never got a reply or an owner push.
    Called on a periodic background loop (see app/main.py); never raises --
    one tenant's failure must not skip the rest or kill the loop."""
    tenants = TenantRepository(database_path).list_active()
    state_repo = OperationStateRepository(database_path)
    for tenant in tenants:
        try:
            _check_and_send_digest_for_tenant(
                tenant=tenant, database_path=database_path, state_repo=state_repo
            )
        except Exception:  # noqa: BLE001 -- one tenant's failure must not skip the rest
            logger.warning(
                "Nightly digest check failed for tenant_id=%s", tenant["id"], exc_info=True
            )


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
