from datetime import datetime, timezone

from app.schemas import InboundMessage, MessageResult


def test_inbound_message_is_platform_neutral() -> None:
    timestamp = datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc)

    message = InboundMessage(
        tenant_id=1,
        tenant_slug="zhen123-house",
        tenant_timezone="Asia/Taipei",
        platform="line",
        platform_user_id="user-123",
        customer_display_name="Spencer",
        text="May I ask about availability?",
        timestamp=timestamp,
    )

    assert message.tenant_id == 1
    assert message.tenant_slug == "zhen123-house"
    assert message.tenant_timezone == "Asia/Taipei"
    assert message.platform == "line"
    assert message.platform_user_id == "user-123"
    assert message.customer_display_name == "Spencer"
    assert message.text == "May I ask about availability?"
    assert message.timestamp == timestamp
    assert "replyToken" not in _schema_fields(InboundMessage)
    assert "line_reply_token" not in _schema_fields(InboundMessage)


def test_inbound_message_customer_display_name_defaults_to_none() -> None:
    message = InboundMessage(
        tenant_id=2,
        tenant_slug="other-villa",
        tenant_timezone="Asia/Taipei",
        platform="line",
        platform_user_id="user-456",
        text="hi",
    )

    assert message.customer_display_name is None
    assert message.timestamp is None


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
