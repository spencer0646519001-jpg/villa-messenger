"""
LINE webhook adapter: translates LINE webhook payloads into platform-neutral
InboundMessage objects.

Contains LINE's payload shape entirely within this module. Nothing LINE-
specific (replyToken, message id, raw event dict) escapes into InboundMessage.
"""

from datetime import datetime, timezone

from app.schemas import InboundMessage


class LineParseError(Exception):
    """Raised when a LINE payload is malformed or unparseable."""


def extract_text_message_events(payload: dict) -> list[dict]:
    """Return payload['events'] filtered to text-message events from a user.
    Raises LineParseError only when 'events' is missing or not a list;
    individual malformed events are skipped (not raised) so one bad event
    does not kill the whole batch."""
    events = payload.get("events")
    if not isinstance(events, list):
        raise LineParseError("payload 'events' missing or not a list")
    return [event for event in events if _is_text_user_message(event)]


def line_event_to_inbound_message(*, event: dict, tenant_id: int, tenant_slug: str, tenant_timezone: str) -> InboundMessage:
    """Translate ONE LINE text-message event into an InboundMessage. Caller
    must pre-filter via extract_text_message_events. LINE-only fields
    (replyToken, message id, mode) are deliberately dropped."""
    return InboundMessage(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        tenant_timezone=tenant_timezone,
        platform="line",
        platform_user_id=event["source"]["userId"],
        customer_display_name=None,
        text=event["message"]["text"],
        timestamp=_parse_timestamp_ms(event["timestamp"]),
    )


def _is_text_user_message(event: object) -> bool:
    """Defensive: returns True iff event is a user-sourced text-message event.
    Malformed individual events return False (skipped, not raised)."""
    try:
        if not isinstance(event, dict):
            return False
        if event.get("type") != "message":
            return False
        if (event.get("message") or {}).get("type") != "text":
            return False
        return (event.get("source") or {}).get("type") == "user"
    except (AttributeError, TypeError):
        return False


def _parse_timestamp_ms(ms: int) -> datetime:
    """LINE webhook events carry timestamps as ms since epoch, UTC."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
