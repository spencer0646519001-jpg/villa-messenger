from datetime import datetime, timezone

from app.schemas import InboundMessage, MessageResult


def test_inbound_message_is_platform_neutral() -> None:
    timestamp = datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc)

    message = InboundMessage(
        tenant_slug="zhen123-house",
        platform="line",
        platform_user_id="user-123",
        text="May I ask about availability?",
        timestamp=timestamp,
    )

    assert message.tenant_slug == "zhen123-house"
    assert message.platform == "line"
    assert message.platform_user_id == "user-123"
    assert message.text == "May I ask about availability?"
    assert message.timestamp == timestamp
    assert "replyToken" not in _schema_fields(InboundMessage)
    assert "line_reply_token" not in _schema_fields(InboundMessage)


def test_message_result_defaults_are_platform_neutral() -> None:
    result = MessageResult(should_reply=True, reply_text="Thanks, staff will confirm soon.")

    assert result.should_reply is True
    assert result.reply_text == "Thanks, staff will confirm soon."
    assert result.should_notify_owner is False
    assert result.owner_notification_text is None
    assert "replyToken" not in _schema_fields(MessageResult)
    assert "line_reply_token" not in _schema_fields(MessageResult)


def _schema_fields(model: type[InboundMessage] | type[MessageResult]) -> set[str]:
    model_fields = getattr(model, "model_fields", None)
    if model_fields is not None:
        return set(model_fields)
    return set(getattr(model, "__fields__", {}))
