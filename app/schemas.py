from datetime import datetime

from pydantic import BaseModel


class InboundMessage(BaseModel):
    tenant_slug: str
    platform: str
    platform_user_id: str
    text: str
    timestamp: datetime | None = None


class MessageResult(BaseModel):
    should_reply: bool
    reply_text: str | None = None
    should_notify_owner: bool = False
    owner_notification_text: str | None = None

