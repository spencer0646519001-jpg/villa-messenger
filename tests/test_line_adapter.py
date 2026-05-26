import inspect
from datetime import datetime, timezone

import pytest

from app.adapters.line_adapter import (
    LineParseError,
    _is_text_user_message,
    _parse_timestamp_ms,
    extract_text_message_events,
    line_event_to_inbound_message,
)
from app.schemas import InboundMessage


# ============================================================
# FIXTURES -- realistic LINE webhook payload shapes
# ============================================================


def _text_event(
    *,
    text: str = "hello",
    user_id: str = "Uabc123",
    timestamp_ms: int = 1716700000000,
    reply_token: str = "rt-xyz",
    message_id: str = "msg-1",
) -> dict:
    return {
        "type": "message",
        "mode": "active",
        "timestamp": timestamp_ms,
        "source": {"type": "user", "userId": user_id},
        "replyToken": reply_token,
        "message": {"type": "text", "id": message_id, "text": text},
    }


def _sticker_event() -> dict:
    return {
        "type": "message",
        "mode": "active",
        "timestamp": 1716700000000,
        "source": {"type": "user", "userId": "Uabc"},
        "replyToken": "rt",
        "message": {"type": "sticker", "id": "msg", "packageId": "1", "stickerId": "1"},
    }


def _image_event() -> dict:
    return {
        "type": "message",
        "mode": "active",
        "timestamp": 1716700000000,
        "source": {"type": "user", "userId": "Uabc"},
        "replyToken": "rt",
        "message": {"type": "image", "id": "msg"},
    }


def _location_event() -> dict:
    return {
        "type": "message",
        "mode": "active",
        "timestamp": 1716700000000,
        "source": {"type": "user", "userId": "Uabc"},
        "replyToken": "rt",
        "message": {"type": "location", "id": "msg", "address": "Taipei"},
    }


def _follow_event() -> dict:
    return {
        "type": "follow",
        "mode": "active",
        "timestamp": 1716700000000,
        "source": {"type": "user", "userId": "Uabc"},
        "replyToken": "rt",
    }


def _unfollow_event() -> dict:
    return {
        "type": "unfollow",
        "mode": "active",
        "timestamp": 1716700000000,
        "source": {"type": "user", "userId": "Uabc"},
    }


def _join_event() -> dict:
    return {
        "type": "join",
        "mode": "active",
        "timestamp": 1716700000000,
        "source": {"type": "group", "groupId": "Gabc"},
        "replyToken": "rt",
    }


def _group_text_event() -> dict:
    return {
        "type": "message",
        "mode": "active",
        "timestamp": 1716700000000,
        "source": {"type": "group", "groupId": "Gabc", "userId": "Uabc"},
        "replyToken": "rt",
        "message": {"type": "text", "id": "msg", "text": "from group"},
    }


def _room_text_event() -> dict:
    return {
        "type": "message",
        "mode": "active",
        "timestamp": 1716700000000,
        "source": {"type": "room", "roomId": "Rabc", "userId": "Uabc"},
        "replyToken": "rt",
        "message": {"type": "text", "id": "msg", "text": "from room"},
    }


# ============================================================
# extract_text_message_events: FILTERING
# ============================================================


def test_single_text_event_is_returned() -> None:
    event = _text_event(text="hi")
    payload = {"destination": "Udest", "events": [event]}

    assert extract_text_message_events(payload) == [event]


def test_sticker_event_filtered_out() -> None:
    assert extract_text_message_events({"events": [_sticker_event()]}) == []


def test_image_event_filtered_out() -> None:
    assert extract_text_message_events({"events": [_image_event()]}) == []


def test_location_event_filtered_out() -> None:
    assert extract_text_message_events({"events": [_location_event()]}) == []


def test_follow_event_filtered_out() -> None:
    assert extract_text_message_events({"events": [_follow_event()]}) == []


def test_unfollow_event_filtered_out() -> None:
    assert extract_text_message_events({"events": [_unfollow_event()]}) == []


def test_join_event_filtered_out() -> None:
    assert extract_text_message_events({"events": [_join_event()]}) == []


def test_group_source_text_event_filtered_out() -> None:
    assert extract_text_message_events({"events": [_group_text_event()]}) == []


def test_room_source_text_event_filtered_out() -> None:
    assert extract_text_message_events({"events": [_room_text_event()]}) == []


def test_mixed_batch_returns_only_text_user_events() -> None:
    text = _text_event(text="keep me")
    payload = {
        "destination": "Udest",
        "events": [
            text,
            _sticker_event(),
            _follow_event(),
            _image_event(),
            _group_text_event(),
        ],
    }

    assert extract_text_message_events(payload) == [text]


def test_empty_events_list_returns_empty_list() -> None:
    assert extract_text_message_events({"events": []}) == []


# ============================================================
# extract_text_message_events: STRUCTURE ERRORS
# ============================================================


def test_missing_events_key_raises_parse_error() -> None:
    with pytest.raises(LineParseError):
        extract_text_message_events({"destination": "Udest"})


def test_events_value_string_raises_parse_error() -> None:
    with pytest.raises(LineParseError):
        extract_text_message_events({"events": "not-a-list"})


def test_events_value_dict_raises_parse_error() -> None:
    with pytest.raises(LineParseError):
        extract_text_message_events({"events": {"oops": "object"}})


def test_events_value_none_raises_parse_error() -> None:
    with pytest.raises(LineParseError):
        extract_text_message_events({"events": None})


def test_individual_malformed_event_is_skipped_not_raised() -> None:
    """One bad event in an otherwise-valid batch must not kill the batch."""
    valid = _text_event(text="survivor")
    malformed = [
        {"type": "message"},
        {"type": "message", "message": None, "source": {"type": "user", "userId": "U"}},
        {"type": "message", "message": {"type": "text", "text": "x"}, "source": None},
        {},
        "not-even-a-dict",
        None,
        42,
    ]
    payload = {"events": malformed + [valid]}

    assert extract_text_message_events(payload) == [valid]


# ============================================================
# line_event_to_inbound_message: MAPPING
# ============================================================


def test_translation_maps_all_neutral_fields() -> None:
    event = _text_event(
        text="May I ask about availability?",
        user_id="U-spencer-123",
        timestamp_ms=1716700000000,
    )

    msg = line_event_to_inbound_message(
        event=event,
        tenant_id=7,
        tenant_slug="zhen123-house",
        tenant_timezone="Asia/Taipei",
    )

    assert isinstance(msg, InboundMessage)
    assert msg.text == "May I ask about availability?"
    assert msg.platform == "line"
    assert msg.platform_user_id == "U-spencer-123"
    assert msg.tenant_id == 7
    assert msg.tenant_slug == "zhen123-house"
    assert msg.tenant_timezone == "Asia/Taipei"
    assert msg.customer_display_name is None


def test_translation_converts_ms_epoch_to_utc_datetime() -> None:
    event = _text_event(timestamp_ms=1716700000000)

    msg = line_event_to_inbound_message(
        event=event,
        tenant_id=1,
        tenant_slug="t",
        tenant_timezone="UTC",
    )

    assert msg.timestamp == datetime(2024, 5, 26, 5, 6, 40, tzinfo=timezone.utc)
    assert msg.timestamp.tzinfo is timezone.utc


def test_parse_timestamp_ms_preserves_subsecond_precision() -> None:
    result = _parse_timestamp_ms(1716700000500)

    assert result == datetime(2024, 5, 26, 5, 6, 40, 500000, tzinfo=timezone.utc)


# ============================================================
# NEUTRAL BOUNDARY -- no LINE-specific data may enter InboundMessage
# ============================================================


def _schema_fields(model) -> set[str]:
    """Mirror of tests/test_schemas.py:57-61."""
    model_fields = getattr(model, "model_fields", None)
    if model_fields is not None:
        return set(model_fields)
    return set(getattr(model, "__fields__", {}))


def test_inbound_message_schema_has_no_line_specific_fields() -> None:
    """Structural enforcement: the platform-neutral schema must not gain LINE-
    specific fields. Mirrors tests/test_schemas.py:28-29."""
    fields = _schema_fields(InboundMessage)

    assert "replyToken" not in fields
    assert "reply_token" not in fields
    assert "line_reply_token" not in fields
    assert "message_id" not in fields
    assert "line_message_id" not in fields
    assert "mode" not in fields
    assert "raw_event" not in fields
    assert "destination" not in fields


def test_translated_message_does_not_leak_line_specific_values() -> None:
    """Runtime check: even when the source event carries replyToken / message
    id, those values must appear nowhere in the resulting model."""
    event = _text_event(reply_token="rt-DO-NOT-LEAK", message_id="msg-LEAK")

    msg = line_event_to_inbound_message(
        event=event,
        tenant_id=1,
        tenant_slug="t",
        tenant_timezone="UTC",
    )

    assert not hasattr(msg, "replyToken")
    assert not hasattr(msg, "reply_token")
    assert not hasattr(msg, "message_id")
    dumped_str = str(msg.model_dump())
    assert "rt-DO-NOT-LEAK" not in dumped_str
    assert "msg-LEAK" not in dumped_str


# ============================================================
# METHOD-LENGTH DISCIPLINE
# ============================================================


def _body_line_count(func) -> int:
    src = inspect.getsource(func)
    lines = [line for line in src.splitlines()[1:] if line.strip() and not line.strip().startswith("#")]
    return len(lines)


@pytest.mark.parametrize(
    "func",
    [
        extract_text_message_events,
        line_event_to_inbound_message,
        _is_text_user_message,
        _parse_timestamp_ms,
    ],
)
def test_methods_under_15_body_lines(func) -> None:
    assert _body_line_count(func) <= 15, (
        f"{func.__qualname__} body too long: {_body_line_count(func)} lines"
    )
