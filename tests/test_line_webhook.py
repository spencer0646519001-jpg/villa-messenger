import base64
import hashlib
import hmac
import inspect
import json
import logging
import shutil
import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.llm.fake_provider import FakeProvider
from app.api import line_webhook_routes
from app.api.dependencies import get_database_path
from app.domain.availability_models import AvailabilityResult, BlockedNight
from app.domain.llm_fallback import TYPE_4_STATE_CONTINUATION_JUDGMENT
from app.domain.llm_provider import LLMOutput
from app.domain.pricing_policy import calculate_price
from app.domain.reply_templates import render_quote_message
from app.domain.reply_text import (
    FULL_HOUSE_MESSAGE,
    MANUAL_REVIEW_MESSAGE,
    MISSING_ROOM_COUNT_MESSAGE,
    OWNER_PENDING_EMPTY_MESSAGE,
    OWNER_RECORD_EMPTY_MESSAGE,
    OWNER_RECORD_UNREPLIED_TEXT,
    OWNER_COMMAND_STATUS_OFF_MESSAGE,
    OWNER_COMMAND_TURN_OFF_MESSAGE,
    OWNER_COMMAND_TURN_ON_MESSAGE,
    OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_UNKNOWN,
    OWNER_PUSH_FULL_HOUSE_PREFIX,
    SINGLE_MISSING_CHECKOUT_MESSAGE,
    SINGLE_MISSING_GUEST_COUNT_MESSAGE,
)
from app.main import app
from app.repositories.conversation_state_repository import ConversationStateRepository
from app.repositories.manual_hold_repository import ManualHoldRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.operation_state_repository import OperationStateRepository
from app.repositories.sqlite import get_connection, init_db
from app.repositories.tenant_channel_repository import TenantChannelRepository
from app.repositories.tenant_repository import TenantRepository
from app.services.conversation_handoff_service import ConversationHandoffService
from app.services.conversation_state_service import ConversationStateService
from app.services.availability_service import AvailabilityCheckOutcome
from app.services.operation_mode_service import OperationModeService
from app.services.tenant_config_loaders import (
    make_tenant_pricing_loader,
    make_tenant_room_policy_loader,
    make_tenant_special_dates_loader,
)


_SECRET_REF = "LINE_TEST_CHANNEL_SECRET"
_SECRET = "test-channel-secret-value"
_DESTINATION = "Udest123"
_TZ = "Asia/Taipei"
_OWNER_ROW_TIME = "2026-05-03T00:00:00+08:00"


# ============================================================
# FIXTURES & HELPERS
# ============================================================


@pytest.fixture
def database_path() -> Iterator[Path]:
    parent_dir = Path("pytest-cache-files-webhook")
    temp_dir = parent_dir / str(uuid.uuid4())
    temp_dir.mkdir(parents=True)
    path = temp_dir / "webhook-tests.db"
    try:
        init_db(path)
        yield path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            parent_dir.rmdir()
        except OSError:
            pass


@pytest.fixture
def client(database_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(_SECRET_REF, _SECRET)
    app.dependency_overrides[get_database_path] = lambda: database_path
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_channel(database_path: Path, *, slug: str = "zhen123-house") -> int:
    tenant_id = TenantRepository(database_path).create_tenant(
        slug=slug,
        name=slug.title(),
        timezone=_TZ,
        default_language="zh-TW",
        emergency_phone="0975-639-757",
    )
    TenantChannelRepository(database_path).create_channel(
        tenant_id=tenant_id,
        platform="line",
        channel_id=_DESTINATION,
        channel_secret_ref=_SECRET_REF,
    )
    return tenant_id


def _set_system_on(database_path: Path, tenant_id: int) -> None:
    service = OperationModeService(repo=OperationStateRepository(database_path))
    service.turn_on(tenant_id=tenant_id, tenant_timezone=_TZ)


def _text_event(
    text: str,
    *,
    user_id: str = "Uguest",
    webhook_event_id: str | None = None,
) -> dict:
    return {
        "type": "message",
        "webhookEventId": webhook_event_id or f"evt-{uuid.uuid4()}",
        "timestamp": 1700000000000,
        "source": {"type": "user", "userId": user_id},
        "message": {"type": "text", "id": "1", "text": text},
    }


def _payload_bytes(events: list[dict], *, destination: str = _DESTINATION) -> bytes:
    payload = {"destination": destination, "events": events}
    return json.dumps(payload).encode("utf-8")


def _sign(body: bytes, secret: str = _SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _post(client: TestClient, body: bytes, signature: str | None) -> object:
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Line-Signature"] = signature
    return client.post("/webhooks/line", content=body, headers=headers)


def _rows(database_path: Path, table: str) -> list[dict]:
    with closing(get_connection(database_path)) as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]


def _force_state_expires_at(database_path: Path, state_id: int, expires_at: str) -> None:
    with closing(get_connection(database_path)) as conn:
        conn.execute(
            "UPDATE conversation_states SET expires_at = ? WHERE id = ?",
            (expires_at, state_id),
        )
        conn.commit()


def _seed_tenant_owner(database_path: Path, tenant_id: int, user_id: str) -> None:
    with closing(get_connection(database_path)) as conn:
        conn.execute(
            """
            INSERT INTO tenant_owners (
                tenant_id, platform, platform_user_id, role, is_active,
                created_at, updated_at
            )
            VALUES (?, 'line', ?, 'owner', 1, ?, ?)
            """,
            (tenant_id, user_id, _OWNER_ROW_TIME, _OWNER_ROW_TIME),
        )
        conn.commit()


def _created_at_from_taipei(year: int, month: int, day: int, hour: int, minute: int) -> str:
    taipei = ZoneInfo(_TZ)
    local = datetime(year, month, day, hour, minute, tzinfo=taipei)
    return local.astimezone(timezone.utc).isoformat()


def _freeze_owner_record_now(
    monkeypatch: pytest.MonkeyPatch,
    *,
    year: int = 2026,
    month: int = 3,
    day: int = 16,
    hour: int = 2,
    minute: int = 30,
) -> None:
    taipei = ZoneInfo(_TZ)
    fixed_now = datetime(year, month, day, hour, minute, tzinfo=taipei)

    def _fixed_now(tenant_timezone: str) -> datetime:
        assert tenant_timezone == _TZ
        return fixed_now

    monkeypatch.setattr(line_webhook_routes, "_now_in_tenant_timezone", _fixed_now)


def _seed_message_at(
    database_path: Path,
    *,
    tenant_id: int,
    user_id: str,
    message_text: str,
    created_at: str,
    reply_text: str | None = None,
) -> None:
    with closing(get_connection(database_path)) as conn:
        conn.execute(
            """
            INSERT INTO messages (
                tenant_id, platform, platform_user_id, message_text,
                category, reply_text, is_night, created_at
            )
            VALUES (?, 'line', ?, ?, 'question', ?, 1, ?)
            """,
            (tenant_id, user_id, message_text, reply_text, created_at),
        )
        conn.commit()


# ============================================================
# CASE 1: valid signature + quote message -> 200 + rows persisted
# ============================================================


def test_valid_quote_persists_message_and_inquiry(client: TestClient, database_path: Path) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    body = _payload_bytes([_text_event("5/12 入住 5/13 退房 4 大人 開2房 多少錢?")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    messages = _rows(database_path, "messages")
    inquiries = _rows(database_path, "inquiries")
    assert len(messages) == 1
    assert len(inquiries) == 1
    # Real config-backed loader ran end-to-end: a concrete quote was produced.
    assert inquiries[0]["estimated_total_price"] is not None


def test_webhook_schedules_pipeline_as_background_task(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    scheduled: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def _capture_add_task(self: object, func: object, *args: object, **kwargs: object) -> None:
        scheduled.append((func, args, kwargs))

    def _pipeline_should_not_run_inline(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("_run_pipeline should only be scheduled")

    monkeypatch.setattr(line_webhook_routes.BackgroundTasks, "add_task", _capture_add_task)
    monkeypatch.setattr(line_webhook_routes, "_run_pipeline", _pipeline_should_not_run_inline)
    body = _payload_bytes([_text_event("hi", webhook_event_id="evt-background")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(scheduled) == 1
    func, args, kwargs = scheduled[0]
    assert func is _pipeline_should_not_run_inline
    assert args[0] == json.loads(body)["events"]
    assert args[1]["id"] == tenant_id
    assert args[2] == database_path
    assert kwargs == {}
    assert _rows(database_path, "messages") == []


# ============================================================
# CASE 2: bad signature -> 400, nothing persisted
# ============================================================


def test_bad_signature_rejected_and_nothing_persisted(client: TestClient, database_path: Path) -> None:
    _seed_channel(database_path)
    body = _payload_bytes([_text_event("5/12 入住 5/13 退房 4 大人 開2房 多少錢?")])

    response = _post(client, body, "not-a-valid-signature")

    assert response.status_code == 400
    assert _rows(database_path, "messages") == []
    assert _rows(database_path, "inquiries") == []


# ============================================================
# CASE 3: unknown channel -> 400
# ============================================================


def test_unknown_channel_rejected(client: TestClient, database_path: Path) -> None:
    _seed_channel(database_path)  # known channel is _DESTINATION; we send another
    body = _payload_bytes([_text_event("hi")], destination="Uunknown")

    response = _post(client, body, _sign(body))

    assert response.status_code == 400
    assert _rows(database_path, "messages") == []


# ============================================================
# CASE 4: non-text event -> 200, nothing persisted
# ============================================================


def test_non_text_event_acknowledged_without_persisting(client: TestClient, database_path: Path) -> None:
    _seed_channel(database_path)
    image_event = {
        "type": "message",
        "timestamp": 1700000000000,
        "source": {"type": "user", "userId": "Uguest"},
        "message": {"type": "image", "id": "2"},
    }
    body = _payload_bytes([image_event])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert _rows(database_path, "messages") == []


# ============================================================
# CASE 5: urgent message persists (clock-independent path)
# ============================================================


def test_urgent_message_persists_as_urgent(client: TestClient, database_path: Path) -> None:
    _seed_channel(database_path)
    body = _payload_bytes([_text_event("房間漏水了怎麼辦")])  # 漏水 = water leak (urgent)

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    messages = _rows(database_path, "messages")
    assert len(messages) == 1
    assert messages[0]["is_urgent"] == 1
    assert messages[0]["category"] == "urgent"
    # Urgent path never reaches the quote pipeline -> no inquiry row.
    assert _rows(database_path, "inquiries") == []


# ============================================================
# OUTWARD-OPAQUE GUARANTEE: all three 400 bodies are byte-identical
# ============================================================


def test_all_400_bodies_are_byte_identical(client: TestClient, database_path: Path) -> None:
    _seed_channel(database_path)
    good_body = _payload_bytes([_text_event("hi")])

    malformed = _post(client, b"{not json", _sign(b"{not json"))
    unknown = _post(client, _payload_bytes([_text_event("hi")], destination="Ux"), _sign(_payload_bytes([_text_event("hi")], destination="Ux")))
    bad_sig = _post(client, good_body, "wrong-signature")

    assert malformed.status_code == unknown.status_code == bad_sig.status_code == 400
    # Outward-opaque: an attacker cannot distinguish which check failed.
    assert malformed.content == unknown.content == bad_sig.content


# ============================================================
# STAGE 2: outbound reply = decision.customer_reply_text
#
# The reply is the field-composed text the decision already carries. It is sent
# ONLY when present (on-mode inquiries that produce a customer reply); off mode /
# do_nothing / push-only decisions carry None and send NOTHING.
#
# The send is best-effort and MUST NOT affect receiving: a failed/skipped send
# still returns 200 and still persists. reply_message is patched so no test
# touches the network. _MISSING_INFO_TEXT is the message used to elicit a
# deterministic, config-independent customer reply.
# ============================================================


_ACCESS_TOKEN_ENV = "LINE_TEST_CHANNEL_ACCESS_TOKEN"
# Full dates + price intent, guest count missing -> single missing-info template.
_MISSING_INFO_TEXT = "5/12 入住 5/14 退房 多少錢?"


def _text_event_with_reply_token(
    text: str,
    reply_token: str = "rtok-123",
    *,
    user_id: str = "Uguest",
    webhook_event_id: str | None = None,
) -> dict:
    event = _text_event(
        text,
        user_id=user_id,
        webhook_event_id=webhook_event_id,
    )
    event["replyToken"] = reply_token
    return event


def test_send_invoked_with_token_and_composed_reply_text(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    calls: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "reply_message", lambda **kw: calls.append(kw))
    body = _payload_bytes([_text_event_with_reply_token(_MISSING_INFO_TEXT)])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["reply_token"] == "rtok-123"
    assert calls[0]["access_token"] == "tok-abc"
    # Exact composed reply, not a hardcoded string.
    assert calls[0]["text"] == SINGLE_MISSING_GUEST_COUNT_MESSAGE
    assert len(_rows(database_path, "messages")) == 1  # receiving unaffected


def test_off_mode_sends_nothing_still_200_and_persisted(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Explicitly force OFF (clock-independent) so customer_reply_text is None.
    tenant_id = _seed_channel(database_path)
    OperationModeService(repo=OperationStateRepository(database_path)).turn_off(
        tenant_id=tenant_id, tenant_timezone=_TZ
    )
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    calls: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "reply_message", lambda **kw: calls.append(kw))
    body = _payload_bytes([_text_event_with_reply_token(_MISSING_INFO_TEXT)])

    response = _post(client, body, _sign(body))

    # Off mode -> no outbound, but receiving + persistence + ack are unchanged.
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert calls == []
    assert len(_rows(database_path, "messages")) == 1


def test_send_failure_rolls_back_dedupe_still_200_and_persisted(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    monkeypatch.setattr(line_webhook_routes, "_REPLY_RETRY_DELAYS_SECONDS", (0, 0))
    attempts: list[dict] = []

    def _boom(**kw: object) -> None:
        attempts.append(kw)
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(line_webhook_routes, "reply_message", _boom)
    body = _payload_bytes(
        [_text_event_with_reply_token(_MISSING_INFO_TEXT, webhook_event_id="evt-send-fails")]
    )

    response = _post(client, body, _sign(body))

    # Send blew up after retries, but receiving + persistence are untouched and
    # the dedupe stamp is removed so a redelivery can try again.
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(attempts) == 3
    assert len(_rows(database_path, "messages")) == 1
    assert _rows(database_path, "processed_webhook_events") == []


def test_missing_reply_token_skips_send_still_200(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    calls: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "reply_message", lambda **kw: calls.append(kw))
    body = _payload_bytes([_text_event(_MISSING_INFO_TEXT)])  # no replyToken

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert calls == []  # skipped, not crashed
    assert len(_rows(database_path, "messages")) == 1


def test_missing_access_token_env_skips_send_still_200(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    monkeypatch.delenv(_ACCESS_TOKEN_ENV, raising=False)
    calls: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "reply_message", lambda **kw: calls.append(kw))
    body = _payload_bytes([_text_event_with_reply_token(_MISSING_INFO_TEXT)])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert calls == []  # no token -> skipped, not crashed
    assert len(_rows(database_path, "messages")) == 1


# ============================================================
# STAGE B: conversation-state accumulation (records state only; reply unchanged)
#
# Two-tier policy: a quote-relevant inquiry OPENS a state; any slot-bearing
# follow-up (even one that does not itself classify as an inquiry) UPDATES it.
# State writing is best-effort: a failure must not break persistence/reply/200.
# ============================================================


# Dates + price intent, guests missing -> quote-relevant, opens a state.
_DATES_ONLY_INQUIRY = "5/12 入住 5/14 退房 多少錢?"


# Price intent + checkin only (checkout + guests missing) -> opens an
# INCOMPLETE state. A bare-guest follow-up then merges in but the accumulation
# stays incomplete (checkout still absent), so this stays a pure STAGE B merge
# test -- the complete -> quote/completed flow is covered in the STAGE C section.
_CHECKIN_ONLY_INQUIRY = "5/12 入住 多少錢"


def test_two_message_accumulation_leaves_one_filled_state(
    client: TestClient, database_path: Path
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)

    body1 = _payload_bytes([_text_event(_CHECKIN_ONLY_INQUIRY)])
    assert _post(client, body1, _sign(body1)).status_code == 200
    body2 = _payload_bytes([_text_event("4 大人")])  # bare follow-up, same user
    assert _post(client, body2, _sign(body2)).status_code == 200

    states = _rows(database_path, "conversation_states")
    assert len(states) == 1
    # Still incomplete (no checkout) -> never completed by STAGE C.
    assert states[0]["status"] == "in_progress"
    assert states[0]["checkin_date"] == "2026-05-12"
    assert states[0]["checkout_date"] is None
    assert states[0]["adult_count"] == 4  # merged from the follow-up


def test_non_inquiry_creates_no_state(client: TestClient, database_path: Path) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    body = _payload_bytes([_text_event("你好")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert _rows(database_path, "conversation_states") == []
    assert len(_rows(database_path, "messages")) == 1  # still persisted


def test_quote_relevant_message_opens_one_state(
    client: TestClient, database_path: Path
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    body = _payload_bytes([_text_event(_DATES_ONLY_INQUIRY)])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    states = _rows(database_path, "conversation_states")
    assert len(states) == 1
    assert states[0]["checkin_date"] == "2026-05-12"


def test_bbq_request_with_pet_persists_wants_bbq_on_first_turn(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Real LINE E2E regression: the reply already answered with the BBQ
    # policy (FAQ topic matcher fires on the bare word "烤肉"), which made it
    # look like the system "knew" the customer wanted BBQ -- but the
    # slot-filling parser (bbq_parser.parse_bbq) failed to recognize "想加
    # 烤肉" as an explicit request, so wants_bbq was never persisted. No
    # second turn is involved here, so this is not a supersede/merge issue.
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    monkeypatch.setattr(line_webhook_routes, "build_llm_provider_from_env", lambda: None)
    body = _payload_bytes([_text_event("我要訂房，想加烤肉，有帶一隻狗")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    states = _rows(database_path, "conversation_states")
    assert len(states) == 1
    assert states[0]["has_pet"] == 1
    assert states[0]["pet_count"] == 1
    assert states[0]["wants_bbq"] == 1


def test_bbq_request_with_dates_and_guests_persists_wants_bbq(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bare word "烤肉" also matches the FAQ topic matcher, so this message
    # (dates + guests + BBQ) hits the rule parser's FAQ/booking-collision
    # trigger (TYPE_3_FAQ_BOOKING_COLLISION) -- on the real live test this
    # was resolved by the actual LLM; mocked here per the requested
    # deterministic-test style so the assertion doesn't depend on live-model
    # classification.
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    provider = FakeProvider(
        LLMOutput(
            intent=None,
            checkin_date=None,
            checkout_date=None,
            adult_count=None,
            child_count=None,
            infant_count=None,
            pet_count=None,
            has_pet=None,
            last_message_text=None,
            is_booking_intent=True,
            needs_clarification=False,
            clarification_reason=None,
        )
    )
    monkeypatch.setattr(line_webhook_routes, "build_llm_provider_from_env", lambda: provider)
    body = _payload_bytes([_text_event("9/20-9/22,8大2小,想加烤肉")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    states = _rows(database_path, "conversation_states")
    assert len(states) == 1
    assert states[0]["wants_bbq"] == 1


def test_state_write_failure_isolated_still_200_and_persisted(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    calls: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "reply_message", lambda **kw: calls.append(kw))

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("state DB exploded")

    monkeypatch.setattr(ConversationStateRepository, "create", _boom)
    monkeypatch.setattr(ConversationStateRepository, "update_slots", _boom)
    body = _payload_bytes([_text_event_with_reply_token(_DATES_ONLY_INQUIRY)])

    response = _post(client, body, _sign(body))

    # State write blew up, but receiving + persistence + reply + ack are intact.
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(_rows(database_path, "messages")) == 1
    assert len(_rows(database_path, "inquiries")) == 1
    assert len(calls) == 1  # reply path unaffected


# ============================================================
# STAGE C: the ACCUMULATED state drives the reply
#
# When the merged state completes, send a quote (from the accumulated slots,
# not just this message's) and mark the state completed; when still incomplete,
# ask for the missing slot. Off mode / urgent stay silent. No active state ->
# the per-message reply (today's behavior). reply_message is patched -> no net.
# ============================================================


# Dates + price, guests missing -> opens an INCOMPLETE state (no quote yet).
_DATES_PRICE_NO_GUESTS = "5/12 入住 5/13 退房 多少錢?"


def _expected_quote(
    database_path: Path,
    tenant_id: int,
    *,
    checkin: str,
    checkout: str,
    adults: int,
    children: int = 0,
    room_count: int = 2,
) -> str:
    """The quote the single-message path would produce for these slots, built
    from the SAME domain functions + config loaders the route uses. Guards
    against STAGE C growing a divergent quote computation."""
    kwargs = dict(
        checkin_date=date.fromisoformat(checkin),
        checkout_date=date.fromisoformat(checkout),
        adult_count=adults,
        child_count=children,
        infant_count=0,
        pet_count=0,
        room_count=room_count,
    )
    pricing = calculate_price(
        **kwargs,
        tenant_pricing=make_tenant_pricing_loader(database_path)(tenant_id),
        room_policy=make_tenant_room_policy_loader(database_path)(tenant_id),
        tenant_special_dates=make_tenant_special_dates_loader(database_path)(tenant_id),
    )
    return render_quote_message(pricing=pricing, **kwargs)


def _capture_replies(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    calls: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "reply_message", lambda **kw: calls.append(kw))
    return calls


def test_stale_in_progress_state_does_not_block_new_round(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    calls = _capture_replies(monkeypatch)
    stale_id = ConversationStateRepository(database_path).create(
        tenant_id=tenant_id,
        platform="line",
        platform_user_id="Uguest",
        checkin_date="2026-07-28",
        checkout_date="2026-07-29",
        adult_count=13,
    )
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _force_state_expires_at(database_path, stale_id, past)

    body = _payload_bytes([
        _text_event_with_reply_token("7/10 入住 7/11 退房 10 大人 2 小孩 多少錢?")
    ])
    assert _post(client, body, _sign(body)).status_code == 200

    states = sorted(_rows(database_path, "conversation_states"), key=lambda row: row["id"])
    assert calls[-1]["text"] == MISSING_ROOM_COUNT_MESSAGE
    assert [row["status"] for row in states] == ["expired", "in_progress"]
    assert states[1]["checkin_date"] == "2026-07-10"
    assert states[1]["checkout_date"] == "2026-07-11"
    assert states[1]["adult_count"] == 10
    assert states[1]["child_count"] == 2


def test_dates_then_guest_count_then_room_count_quotes_without_reasking_dates(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    calls = _capture_replies(monkeypatch)

    body1 = _payload_bytes([_text_event_with_reply_token("7/10入住7/11退房")])
    assert _post(client, body1, _sign(body1)).status_code == 200

    assert calls[-1]["text"] == SINGLE_MISSING_GUEST_COUNT_MESSAGE
    states = _rows(database_path, "conversation_states")
    assert states[0]["checkin_date"] == "2026-07-10"
    assert states[0]["checkout_date"] == "2026-07-11"
    assert states[0]["adult_count"] is None

    body2 = _payload_bytes([_text_event_with_reply_token("10大人2小孩")])
    assert _post(client, body2, _sign(body2)).status_code == 200

    assert calls[-1]["text"] == MISSING_ROOM_COUNT_MESSAGE
    states = _rows(database_path, "conversation_states")
    assert states[0]["checkin_date"] == "2026-07-10"
    assert states[0]["checkout_date"] == "2026-07-11"
    assert states[0]["adult_count"] == 10
    assert states[0]["child_count"] == 2
    assert states[0]["room_count"] is None

    body3 = _payload_bytes([_text_event_with_reply_token("開4房")])
    assert _post(client, body3, _sign(body3)).status_code == 200

    assert calls[-1]["text"] == _expected_quote(
        database_path, tenant_id, checkin="2026-07-10",
        checkout="2026-07-11", adults=10, children=2, room_count=4,
    )
    states = _rows(database_path, "conversation_states")
    assert states[0]["status"] == "completed"


def test_room_count_prompt_accepts_bare_number_followup(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    calls = _capture_replies(monkeypatch)

    body1 = _payload_bytes([
        _text_event_with_reply_token("5/12 入住 5/13 退房 4 大人 多少錢?")
    ])
    assert _post(client, body1, _sign(body1)).status_code == 200
    body2 = _payload_bytes([_text_event_with_reply_token("4")])
    assert _post(client, body2, _sign(body2)).status_code == 200

    assert calls[0]["text"] == MISSING_ROOM_COUNT_MESSAGE
    assert calls[-1]["text"] == _expected_quote(
        database_path, tenant_id, checkin="2026-05-12",
        checkout="2026-05-13", adults=4, room_count=4,
    )
    states = _rows(database_path, "conversation_states")
    assert states[0]["room_count"] == 4
    assert states[0]["status"] == "completed"


def test_booking_signal_generic_question_asks_checkout_then_room_count_and_quotes(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    calls = _capture_replies(monkeypatch)

    body1 = _payload_bytes([_text_event_with_reply_token("12人 7/10號可以嗎?")])
    assert _post(client, body1, _sign(body1)).status_code == 200

    assert calls[-1]["text"] == SINGLE_MISSING_CHECKOUT_MESSAGE
    states = _rows(database_path, "conversation_states")
    assert states[0]["checkin_date"] == "2026-07-10"
    assert states[0]["adult_count"] == 12
    assert states[0]["checkout_date"] is None

    body2 = _payload_bytes([_text_event_with_reply_token("7/11 退房")])
    assert _post(client, body2, _sign(body2)).status_code == 200

    assert calls[-1]["text"] == MISSING_ROOM_COUNT_MESSAGE
    states = _rows(database_path, "conversation_states")
    assert states[0]["checkin_date"] == "2026-07-10"
    assert states[0]["checkout_date"] == "2026-07-11"
    assert states[0]["room_count"] is None

    body3 = _payload_bytes([_text_event_with_reply_token("開4房")])
    assert _post(client, body3, _sign(body3)).status_code == 200

    assert calls[-1]["text"] == _expected_quote(
        database_path, tenant_id, checkin="2026-07-10",
        checkout="2026-07-11", adults=12, room_count=4,
    )
    states = _rows(database_path, "conversation_states")
    assert states[0]["status"] == "completed"


def test_two_message_complete_flow_quotes_from_accumulation_and_completes(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    calls = _capture_replies(monkeypatch)

    body1 = _payload_bytes([_text_event_with_reply_token(_DATES_PRICE_NO_GUESTS)])
    assert _post(client, body1, _sign(body1)).status_code == 200
    body2 = _payload_bytes([_text_event_with_reply_token("4 大人 開2房")])  # completes it
    assert _post(client, body2, _sign(body2)).status_code == 200

    # The SECOND reply is a quote reflecting accumulated dates (msg1) + guests
    # (msg2) -- equal to the single-message path's quote for the same slots.
    assert calls[-1]["text"] == _expected_quote(
        database_path, tenant_id, checkin="2026-05-12", checkout="2026-05-13", adults=4
    )
    states = _rows(database_path, "conversation_states")
    assert len(states) == 1
    assert states[0]["status"] == "completed"  # marked done after the quote


def test_two_message_incomplete_prompts_for_missing_slot(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    monkeypatch.setattr(line_webhook_routes, "build_llm_provider_from_env", lambda: None)
    calls = _capture_replies(monkeypatch)

    # Opens with checkin + price (checkout + guests missing); a bare-guest
    # follow-up fills guests but checkout is still absent -> ask for checkout.
    body1 = _payload_bytes([_text_event_with_reply_token(_CHECKIN_ONLY_INQUIRY)])
    assert _post(client, body1, _sign(body1)).status_code == 200
    body2 = _payload_bytes([_text_event_with_reply_token("4 大人")])
    assert _post(client, body2, _sign(body2)).status_code == 200

    # Prompt is driven by what's missing in the ACCUMULATED state (checkout).
    assert calls[-1]["text"] == SINGLE_MISSING_CHECKOUT_MESSAGE
    states = _rows(database_path, "conversation_states")
    assert states[0]["status"] == "in_progress"  # not completed -- still missing


def _fake_state_continuation_provider(is_booking_intent: bool | None) -> FakeProvider:
    return FakeProvider(
        LLMOutput(
            intent=None,
            checkin_date=None,
            checkout_date=None,
            adult_count=None,
            child_count=None,
            infant_count=None,
            pet_count=None,
            has_pet=None,
            last_message_text=None,
            is_booking_intent=is_booking_intent,
            needs_clarification=False,
            clarification_reason=None,
        )
    )


_OFF_TOPIC_FOLLOWUP = "今天天氣真好呢"


def test_off_topic_followup_against_open_state_llm_says_not_continuing_silences_and_pushes(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    provider = _fake_state_continuation_provider(is_booking_intent=False)
    monkeypatch.setattr(line_webhook_routes, "build_llm_provider_from_env", lambda: provider)
    replies, pushes = _capture_sends(monkeypatch, owner_id="Uowner")

    body1 = _payload_bytes([_text_event_with_reply_token(_DATES_PRICE_NO_GUESTS)])
    assert _post(client, body1, _sign(body1)).status_code == 200
    assert replies[-1]["text"] == SINGLE_MISSING_GUEST_COUNT_MESSAGE

    body2 = _payload_bytes([_text_event_with_reply_token(_OFF_TOPIC_FOLLOWUP)])
    assert _post(client, body2, _sign(body2)).status_code == 200

    # No second customer reply -- the missing-guest-count nag was NOT reissued.
    assert len(replies) == 1
    assert len(pushes) == 1
    assert _OFF_TOPIC_FOLLOWUP in pushes[0]["text"]
    assert provider.calls[-1]["trigger"] == TYPE_4_STATE_CONTINUATION_JUDGMENT
    # The state stays open (not completed) -- info from turn 1 is not thrown away.
    states = _rows(database_path, "conversation_states")
    assert states[0]["status"] == "in_progress"
    assert states[0]["checkin_date"] == "2026-05-12"


def test_off_topic_followup_against_open_state_llm_says_still_continuing_keeps_nagging(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    provider = _fake_state_continuation_provider(is_booking_intent=True)
    monkeypatch.setattr(line_webhook_routes, "build_llm_provider_from_env", lambda: provider)
    replies, pushes = _capture_sends(monkeypatch, owner_id="Uowner")

    body1 = _payload_bytes([_text_event_with_reply_token(_DATES_PRICE_NO_GUESTS)])
    assert _post(client, body1, _sign(body1)).status_code == 200
    body2 = _payload_bytes([_text_event_with_reply_token(_OFF_TOPIC_FOLLOWUP)])
    assert _post(client, body2, _sign(body2)).status_code == 200

    # LLM says the customer is still engaged -> today's behavior is unchanged.
    assert [call["text"] for call in replies] == [
        SINGLE_MISSING_GUEST_COUNT_MESSAGE,
        SINGLE_MISSING_GUEST_COUNT_MESSAGE,
    ]
    assert pushes == []


def test_off_topic_followup_against_open_state_llm_unavailable_keeps_nagging(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moat guarantee: LLM disabled/unavailable must fall back to today's
    rule-based behavior unchanged, never to a false "customer went silent"."""
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    monkeypatch.setattr(line_webhook_routes, "build_llm_provider_from_env", lambda: None)
    replies, pushes = _capture_sends(monkeypatch, owner_id="Uowner")

    body1 = _payload_bytes([_text_event_with_reply_token(_DATES_PRICE_NO_GUESTS)])
    assert _post(client, body1, _sign(body1)).status_code == 200
    body2 = _payload_bytes([_text_event_with_reply_token(_OFF_TOPIC_FOLLOWUP)])
    assert _post(client, body2, _sign(body2)).status_code == 200

    assert [call["text"] for call in replies] == [
        SINGLE_MISSING_GUEST_COUNT_MESSAGE,
        SINGLE_MISSING_GUEST_COUNT_MESSAGE,
    ]
    assert pushes == []


def test_bare_slot_followup_against_open_state_never_consults_the_llm_gate(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine slot-filling reply (e.g. a bare guest count) must keep working
    exactly as before -- the off-topic gate is a rule-based pre-filter and
    should never even call the LLM for a message that fills a missing slot."""
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    provider = _fake_state_continuation_provider(is_booking_intent=False)
    monkeypatch.setattr(line_webhook_routes, "build_llm_provider_from_env", lambda: provider)
    replies, pushes = _capture_sends(monkeypatch, owner_id="Uowner")

    body1 = _payload_bytes([_text_event_with_reply_token(_DATES_PRICE_NO_GUESTS)])
    assert _post(client, body1, _sign(body1)).status_code == 200
    body2 = _payload_bytes([_text_event_with_reply_token("4 大人")])
    assert _post(client, body2, _sign(body2)).status_code == 200

    assert replies[-1]["text"] == MISSING_ROOM_COUNT_MESSAGE
    assert pushes == []
    assert provider.calls == []


def test_off_mode_complete_state_stays_silent_but_accumulates(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    OperationModeService(repo=OperationStateRepository(database_path)).turn_off(
        tenant_id=tenant_id, tenant_timezone=_TZ
    )
    calls = _capture_replies(monkeypatch)

    # A SINGLE complete inquiry in off mode: slots are complete, but off mode is
    # receive-only -> no outbound, even though it would otherwise quote.
    complete = "5/12 入住 5/13 退房 4 大人 開2房 多少錢?"
    body = _payload_bytes([_text_event_with_reply_token(complete)])
    assert _post(client, body, _sign(body)).status_code == 200

    assert calls == []  # silent
    states = _rows(database_path, "conversation_states")
    assert len(states) == 1  # but state still accumulated
    assert states[0]["status"] == "in_progress"  # never completed (no quote sent)


def test_no_active_state_falls_back_to_per_message_reply(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    calls = _capture_replies(monkeypatch)

    # Non-inquiry chatter opens no state -> compose returns the per-message
    # reply unchanged (None here -> nothing sent), exactly as before STAGE C.
    body = _payload_bytes([_text_event_with_reply_token("你好")])
    assert _post(client, body, _sign(body)).status_code == 200

    assert calls == []
    assert _rows(database_path, "conversation_states") == []


def test_single_complete_message_quotes_once_and_completes(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    calls = _capture_replies(monkeypatch)

    # Unified path: a single complete message flows through the state-driven
    # reply too -> one quote, state immediately completed.
    body = _payload_bytes([_text_event_with_reply_token("5/12 入住 5/13 退房 4 大人 開2房 多少錢?")])
    assert _post(client, body, _sign(body)).status_code == 200

    assert len(calls) == 1
    assert calls[0]["text"] == _expected_quote(
        database_path, tenant_id, checkin="2026-05-12", checkout="2026-05-13", adults=4
    )
    states = _rows(database_path, "conversation_states")
    assert len(states) == 1
    assert states[0]["status"] == "completed"


def test_manual_review_completes_state_and_next_message_starts_fresh(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch)

    body1 = _payload_bytes([
        _text_event_with_reply_token("7/28 入住 7/29 退房 8 大人 開1房 多少錢?")
    ])
    assert _post(client, body1, _sign(body1)).status_code == 200

    states = sorted(_rows(database_path, "conversation_states"), key=lambda row: row["id"])
    assert len(states) == 1
    assert states[0]["status"] == "completed"
    assert states[0]["room_count"] == 1
    assert replies[0]["text"] == MANUAL_REVIEW_MESSAGE
    assert len(pushes) == 1

    body2 = _payload_bytes([_text_event_with_reply_token("你好")])
    assert _post(client, body2, _sign(body2)).status_code == 200
    assert len(replies) == 1
    assert all(row["status"] != "in_progress" for row in _rows(database_path, "conversation_states"))

    body3 = _payload_bytes([
        _text_event_with_reply_token("7/28 入住 7/29 退房 13 大人 多少錢?")
    ])
    assert _post(client, body3, _sign(body3)).status_code == 200

    assert replies[-1]["text"] == MISSING_ROOM_COUNT_MESSAGE
    states = sorted(_rows(database_path, "conversation_states"), key=lambda row: row["id"])
    assert [row["status"] for row in states] == ["completed", "in_progress"]
    assert states[1]["adult_count"] == 13
    assert states[1]["room_count"] is None

    body4 = _payload_bytes([_text_event_with_reply_token("開4房")])
    assert _post(client, body4, _sign(body4)).status_code == 200

    assert replies[-1]["text"] == _expected_quote(
        database_path, tenant_id, checkin="2026-07-28",
        checkout="2026-07-29", adults=13, room_count=4,
    )
    states = sorted(_rows(database_path, "conversation_states"), key=lambda row: row["id"])
    assert [row["status"] for row in states] == ["completed", "completed"]
    assert states[1]["room_count"] == 4


def test_mark_completed_failure_isolated_reply_still_sent(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    calls = _capture_replies(monkeypatch)

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("mark_completed exploded")

    monkeypatch.setattr(ConversationStateService, "mark_completed", _boom)
    body = _payload_bytes([_text_event_with_reply_token("5/12 入住 5/13 退房 4 大人 開2房 多少錢?")])

    response = _post(client, body, _sign(body))

    # Send-first: the quote already went out; the mark failure is swallowed.
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["text"] == _expected_quote(
        database_path, tenant_id, checkin="2026-05-12", checkout="2026-05-13", adults=4
    )


def test_urgent_with_active_state_not_overridden_by_quote(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    calls = _capture_replies(monkeypatch)

    # Open an active (incomplete) state, then an urgent message from same user:
    # the urgent path must NOT be hijacked into a customer-facing quote.
    body1 = _payload_bytes([_text_event_with_reply_token(_DATES_PRICE_NO_GUESTS)])
    assert _post(client, body1, _sign(body1)).status_code == 200
    calls.clear()
    body2 = _payload_bytes([_text_event_with_reply_token("火災!!")])  # urgent
    assert _post(client, body2, _sign(body2)).status_code == 200

    assert calls == []  # urgent -> no customer reply (owner push path untouched)
    states = _rows(database_path, "conversation_states")
    assert states[0]["status"] == "in_progress"  # not completed by the urgent msg


# ============================================================
# STAGE D: whitelist FAQ answering + owner push (push-first truthfulness)
#
# Tier-1 (breakfast/checkout/pets): config-driven answer, NO push.
# Tier-2 (wifi/parking) + non-whitelist faq: confirm-and-defer; the route pushes
# the owner FIRST and only keeps the "已通知" wording if the push succeeds.
# Owner-push failure must never break the customer reply, persistence, or 200.
# Both reply_message and push_message are patched -> no network.
# ============================================================


_OWNER_USER_ID_ENV = "LINE_TEST_OWNER_USER_ID"
_NOTIFIED = "已通知服務人員"


def _capture_sends(
    monkeypatch: pytest.MonkeyPatch,
    *,
    push_raises: bool = False,
    owner_id: str | None = "Uowner",
    push_raises_for: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Patch reply_message + push_message; set the tokens/owner-id env. Returns
    (reply_calls, push_calls)."""
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    if owner_id is not None:
        monkeypatch.setenv(_OWNER_USER_ID_ENV, owner_id)
    else:
        monkeypatch.delenv(_OWNER_USER_ID_ENV, raising=False)
    replies: list[dict] = []
    pushes: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "reply_message", lambda **kw: replies.append(kw))

    def _push(**kw: object) -> None:
        pushes.append(kw)
        should_raise_for_owner = kw.get("to_user_id") in (push_raises_for or set())
        if push_raises or should_raise_for_owner:
            raise httpx.ConnectError("push network down")

    monkeypatch.setattr(line_webhook_routes, "push_message", _push)
    return replies, pushes


class _WebhookFakeAvailabilityService:
    def __init__(self, *, outcome: AvailabilityCheckOutcome) -> None:
        self._outcome = outcome
        self.enabled = True
        self.calls: list[tuple[date, date]] = []

    def check(self, *, checkin_date: date, checkout_date: date) -> AvailabilityCheckOutcome:
        self.calls.append((checkin_date, checkout_date))
        return self._outcome


def _webhook_blocked_outcome(
    night: date = date(2026, 5, 12),
) -> AvailabilityCheckOutcome:
    return AvailabilityCheckOutcome(
        status="blocked",
        result=AvailabilityResult(
            has_any_blocked_nights=True,
            blocked_nights=[
                BlockedNight(
                    night_date=night,
                    blocking_event_summary="枕23",
                    matched_keyword="枕",
                )
            ],
        ),
    )


def test_early_date_range_blocked_replies_pushes_owner_and_completes_state(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch)
    fake_availability = _WebhookFakeAvailabilityService(outcome=_webhook_blocked_outcome())
    monkeypatch.setattr(
        line_webhook_routes,
        "_build_availability_service",
        lambda _tenant: fake_availability,
    )
    body = _payload_bytes([_text_event_with_reply_token(_MISSING_INFO_TEXT)])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert replies[-1]["text"] == FULL_HOUSE_MESSAGE
    assert len(pushes) == 1
    assert pushes[0]["to_user_id"] == "Uowner"
    assert OWNER_PUSH_FULL_HOUSE_PREFIX in pushes[0]["text"]
    assert OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_UNKNOWN in pushes[0]["text"]
    assert "Uguest" not in pushes[0]["text"]  # raw userId never printed
    assert fake_availability.calls == [(date(2026, 5, 12), date(2026, 5, 14))]
    states = _rows(database_path, "conversation_states")
    assert states[0]["status"] == "completed"


def test_whole_house_single_date_collision_blocked_checks_assumed_night_end_to_end(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch)
    fake_availability = _WebhookFakeAvailabilityService(
        outcome=_webhook_blocked_outcome(date(2026, 8, 15))
    )
    monkeypatch.setattr(
        line_webhook_routes, "_build_availability_service",
        lambda _tenant: fake_availability,
    )
    monkeypatch.setattr(
        line_webhook_routes, "build_llm_provider_from_env", lambda: None
    )
    text = "您好,請問8/15是否還可以包棟嗎?人數9位,謝謝"
    body = _payload_bytes([_text_event_with_reply_token(text)])

    assert _post(client, body, _sign(body)).status_code == 200

    assert fake_availability.calls == [(date(2026, 8, 15), date(2026, 8, 16))]
    assert "8/15" in replies[-1]["text"]
    assert "8/16" in replies[-1]["text"]
    assert "一次只接待一組客人" not in replies[-1]["text"]
    assert len(pushes) == 1
    states = _rows(database_path, "conversation_states")
    assert states[0]["status"] == "completed"
    assert states[0]["checkin_date"] == "2026-08-15"
    assert states[0]["checkout_date"] is None
    assert states[0]["adult_count"] == 9
    inquiry = _rows(database_path, "inquiries")[0]
    assert inquiry["inquiry_type"] == "availability"
    assert inquiry["checkout_date"] is None
    raw_log = json.loads(_rows(database_path, "messages")[0]["raw_log_payload"])
    assert raw_log["availability_probe_checkout"] == "2026-08-16"
    assert raw_log["availability_probe_checkout_was_inferred"] is True


def test_whole_house_single_date_collision_available_still_asks_checkout(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch)
    fake_availability = _WebhookFakeAvailabilityService(
        outcome=AvailabilityCheckOutcome(
            status="available",
            result=AvailabilityResult(
                has_any_blocked_nights=False, blocked_nights=[]
            ),
        )
    )
    monkeypatch.setattr(
        line_webhook_routes, "_build_availability_service",
        lambda _tenant: fake_availability,
    )
    monkeypatch.setattr(
        line_webhook_routes, "build_llm_provider_from_env", lambda: None
    )
    body = _payload_bytes([
        _text_event_with_reply_token("您好,請問8/15是否還可以包棟嗎?人數9位,謝謝")
    ])

    assert _post(client, body, _sign(body)).status_code == 200

    assert replies[-1]["text"] == SINGLE_MISSING_CHECKOUT_MESSAGE
    assert pushes == []
    assert fake_availability.calls == [(date(2026, 8, 15), date(2026, 8, 16))]
    state = _rows(database_path, "conversation_states")[0]
    assert state["status"] == "in_progress"
    assert state["checkin_date"] == "2026-08-15"
    assert state["checkout_date"] is None
    assert state["adult_count"] == 9


def test_duplicate_webhook_event_id_is_processed_once_for_guest_message(
    client: TestClient,
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch)
    webhook_event_id = "evt-guest-duplicate"
    first_body = _payload_bytes(
        [
            _text_event_with_reply_token(
                _MISSING_INFO_TEXT,
                "rtok-first",
                webhook_event_id=webhook_event_id,
            )
        ]
    )
    second_body = _payload_bytes(
        [
            _text_event_with_reply_token(
                _MISSING_INFO_TEXT,
                "rtok-second",
                webhook_event_id=webhook_event_id,
            )
        ]
    )

    with caplog.at_level(logging.INFO, logger=line_webhook_routes.__name__):
        first_response = _post(client, first_body, _sign(first_body))
        second_response = _post(client, second_body, _sign(second_body))

    messages = _rows(database_path, "messages")
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert len(replies) == 1
    assert replies[0]["reply_token"] == "rtok-first"
    assert replies[0]["text"] == SINGLE_MISSING_GUEST_COUNT_MESSAGE
    assert pushes == []
    assert len(messages) == 1
    assert messages[0]["message_text"] == _MISSING_INFO_TEXT
    assert len(_rows(database_path, "processed_webhook_events")) == 1
    assert webhook_event_id in caplog.text


def test_reply_failure_rollback_allows_redelivery_of_same_webhook_event_id(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    monkeypatch.setattr(line_webhook_routes, "_REPLY_RETRY_DELAYS_SECONDS", (0, 0))
    attempts: list[dict] = []
    webhook_event_id = "evt-redelivery-after-reply-failure"

    def _fail_first_delivery(**kw: object) -> None:
        attempts.append(kw)
        if len(attempts) <= 3:
            raise httpx.ConnectError("reply network down")

    monkeypatch.setattr(line_webhook_routes, "reply_message", _fail_first_delivery)
    first_body = _payload_bytes(
        [
            _text_event_with_reply_token(
                _MISSING_INFO_TEXT,
                "rtok-first",
                webhook_event_id=webhook_event_id,
            )
        ]
    )
    second_body = _payload_bytes(
        [
            _text_event_with_reply_token(
                _MISSING_INFO_TEXT,
                "rtok-redelivery",
                webhook_event_id=webhook_event_id,
            )
        ]
    )

    first_response = _post(client, first_body, _sign(first_body))
    assert first_response.status_code == 200
    assert [attempt["reply_token"] for attempt in attempts] == ["rtok-first"] * 3
    assert _rows(database_path, "processed_webhook_events") == []

    second_response = _post(client, second_body, _sign(second_body))

    assert second_response.status_code == 200
    assert attempts[-1]["reply_token"] == "rtok-redelivery"
    assert attempts[-1]["text"] == SINGLE_MISSING_GUEST_COUNT_MESSAGE
    assert len(_rows(database_path, "messages")) == 2
    assert len(_rows(database_path, "processed_webhook_events")) == 1


def test_pipeline_exception_keeps_dedupe_and_notifies_owner(
    client: TestClient,
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tenant_id = _seed_channel(database_path)
    replies, pushes = _capture_sends(monkeypatch)
    webhook_event_id = "evt-pipeline-explodes"

    def _boom(**_kw: object) -> None:
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(line_webhook_routes, "_process_pipeline_event", _boom)
    body = _payload_bytes(
        [_text_event_with_reply_token("hello", webhook_event_id=webhook_event_id)]
    )

    with caplog.at_level(logging.ERROR, logger=line_webhook_routes.__name__):
        response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert replies == []
    assert len(pushes) == 1
    assert pushes[0]["to_user_id"] == "Uowner"
    assert pushes[0]["text"] == line_webhook_routes._PIPELINE_FAILURE_OWNER_NOTICE
    assert "hello" not in pushes[0]["text"]
    assert _rows(database_path, "messages") == []
    assert len(_rows(database_path, "processed_webhook_events")) == 1
    assert webhook_event_id in caplog.text
    assert "RuntimeError: pipeline exploded" in caplog.text


def test_pipeline_exception_owner_notification_failure_is_swallowed(
    client: TestClient,
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_channel(database_path)

    def _pipeline_boom(**_kw: object) -> None:
        raise RuntimeError("pipeline exploded")

    def _owner_push_boom(**_kw: object) -> bool:
        raise RuntimeError("owner push exploded")

    monkeypatch.setattr(line_webhook_routes, "_process_pipeline_event", _pipeline_boom)
    monkeypatch.setattr(line_webhook_routes, "_send_owner_push", _owner_push_boom)
    body = _payload_bytes(
        [_text_event_with_reply_token("hello", webhook_event_id="evt-owner-notify-fails")]
    )

    with caplog.at_level(logging.WARNING, logger=line_webhook_routes.__name__):
        response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert len(_rows(database_path, "processed_webhook_events")) == 1
    assert "RuntimeError: pipeline exploded" in caplog.text
    assert "LINE owner push for pipeline failure failed" in caplog.text
    assert "RuntimeError: owner push exploded" in caplog.text


def test_duplicate_webhook_event_id_skips_owner_command_replay(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, pushes = _capture_sends(monkeypatch)
    webhook_event_id = "evt-owner-on-duplicate"
    first_body = _payload_bytes(
        [_text_event_with_reply_token("/開機", user_id="Uowner-a", webhook_event_id=webhook_event_id)]
    )
    second_body = _payload_bytes(
        [_text_event_with_reply_token("/開機", user_id="Uowner-a", webhook_event_id=webhook_event_id)]
    )

    first_response = _post(client, first_body, _sign(first_body))
    second_response = _post(client, second_body, _sign(second_body))

    row = OperationStateRepository(database_path).get_or_create(tenant_id)
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert row["manual_mode"] == "on"
    assert replies == []
    assert len(pushes) == 1
    assert pushes[0]["to_user_id"] == "Uowner-a"
    assert pushes[0]["text"] == OWNER_COMMAND_TURN_ON_MESSAGE
    assert _rows(database_path, "messages") == []
    assert len(_rows(database_path, "processed_webhook_events")) == 1


def test_different_webhook_event_ids_are_both_processed(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch)
    first_body = _payload_bytes(
        [
            _text_event_with_reply_token(
                _MISSING_INFO_TEXT,
                "rtok-first",
                webhook_event_id="evt-guest-first",
            )
        ]
    )
    second_body = _payload_bytes(
        [
            _text_event_with_reply_token(
                _MISSING_INFO_TEXT,
                "rtok-second",
                webhook_event_id="evt-guest-second",
            )
        ]
    )

    first_response = _post(client, first_body, _sign(first_body))
    second_response = _post(client, second_body, _sign(second_body))

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert [reply["reply_token"] for reply in replies] == ["rtok-first", "rtok-second"]
    assert pushes == []
    assert len(_rows(database_path, "messages")) == 2
    assert len(_rows(database_path, "processed_webhook_events")) == 2


def test_missing_webhook_event_id_fails_open_and_logs_warning(
    client: TestClient,
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch)
    event = _text_event_with_reply_token(_MISSING_INFO_TEXT)
    del event["webhookEventId"]
    body = _payload_bytes([event])

    with caplog.at_level(logging.WARNING, logger=line_webhook_routes.__name__):
        response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert len(replies) == 1
    assert replies[0]["text"] == SINGLE_MISSING_GUEST_COUNT_MESSAGE
    assert pushes == []
    assert len(_rows(database_path, "messages")) == 1
    assert _rows(database_path, "processed_webhook_events") == []
    assert "missing webhookEventId" in caplog.text


def test_non_owner_command_is_invisible_and_flows_normally(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    OperationModeService(repo=OperationStateRepository(database_path)).turn_off(
        tenant_id=tenant_id, tenant_timezone=_TZ
    )
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, pushes = _capture_sends(monkeypatch, owner_id=None)

    def _unexpected_turn_on(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("non-owner command must not call turn_on")

    monkeypatch.setattr(OperationModeService, "turn_on", _unexpected_turn_on)
    body = _payload_bytes([_text_event_with_reply_token("/開機", user_id="Uguest")])

    response = _post(client, body, _sign(body))

    row = OperationStateRepository(database_path).get_or_create(tenant_id)
    assert response.status_code == 200
    assert row["manual_mode"] == "off"
    assert replies == []
    assert pushes == []
    assert len(_rows(database_path, "messages")) == 1


def test_owner_turn_on_command_pushes_all_owners_and_skips_pipeline(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_tenant_owner(database_path, tenant_id, "Uowner-b")
    replies, pushes = _capture_sends(monkeypatch, owner_id="Ufallback-owner")
    body = _payload_bytes([_text_event_with_reply_token("/開機", user_id="Uowner-a")])

    response = _post(client, body, _sign(body))

    row = OperationStateRepository(database_path).get_or_create(tenant_id)
    assert response.status_code == 200
    assert row["manual_mode"] == "on"
    assert row["last_changed_by_owner_id"] is None
    assert replies == []
    assert [push["to_user_id"] for push in pushes] == ["Uowner-a", "Uowner-b"]
    assert [push["text"] for push in pushes] == [
        OWNER_COMMAND_TURN_ON_MESSAGE,
        OWNER_COMMAND_TURN_ON_MESSAGE,
    ]
    assert _rows(database_path, "messages") == []


def test_owner_turn_off_command_pushes_all_owners_and_skips_pipeline(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_tenant_owner(database_path, tenant_id, "Uowner-b")
    replies, pushes = _capture_sends(monkeypatch, owner_id="Ufallback-owner")
    body = _payload_bytes([_text_event_with_reply_token("/關機", user_id="Uowner-b")])

    response = _post(client, body, _sign(body))

    row = OperationStateRepository(database_path).get_or_create(tenant_id)
    assert response.status_code == 200
    assert row["manual_mode"] == "off"
    assert row["last_changed_by_owner_id"] is None
    assert replies == []
    assert [push["to_user_id"] for push in pushes] == ["Uowner-a", "Uowner-b"]
    assert [push["text"] for push in pushes] == [
        OWNER_COMMAND_TURN_OFF_MESSAGE,
        OWNER_COMMAND_TURN_OFF_MESSAGE,
    ]
    assert _rows(database_path, "messages") == []


def test_owner_status_command_replies_only_to_sender_and_does_not_change_state(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    OperationModeService(repo=OperationStateRepository(database_path)).turn_off(
        tenant_id=tenant_id, tenant_timezone=_TZ
    )
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_tenant_owner(database_path, tenant_id, "Uowner-b")
    before = OperationStateRepository(database_path).get_or_create(tenant_id)
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes(
        [_text_event_with_reply_token("/狀態", "rt-status", user_id="Uowner-a")]
    )

    response = _post(client, body, _sign(body))

    after = OperationStateRepository(database_path).get_or_create(tenant_id)
    assert response.status_code == 200
    assert after == before
    assert len(replies) == 1
    assert replies[0]["reply_token"] == "rt-status"
    assert replies[0]["text"] == OWNER_COMMAND_STATUS_OFF_MESSAGE
    assert pushes == []
    assert _rows(database_path, "messages") == []


def test_owner_record_command_replies_with_cross_midnight_non_owner_messages(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _freeze_owner_record_now(monkeypatch, hour=2, minute=30)
    _seed_message_at(
        database_path,
        tenant_id=tenant_id,
        user_id="Uguest-day",
        message_text="白天訊息不應出現",
        created_at=_created_at_from_taipei(2026, 3, 15, 10, 0),
        reply_text="day reply",
    )
    _seed_message_at(
        database_path,
        tenant_id=tenant_id,
        user_id="Uguest-a",
        message_text="請問還有空房嗎",
        created_at=_created_at_from_taipei(2026, 3, 15, 23, 41),
        reply_text="目前仍有空房",
    )
    _seed_message_at(
        database_path,
        tenant_id=tenant_id,
        user_id="Uowner-a",
        message_text="owner 自己的訊息不應出現",
        created_at=_created_at_from_taipei(2026, 3, 16, 1, 0),
        reply_text="owner reply",
    )
    _seed_message_at(
        database_path,
        tenant_id=tenant_id,
        user_id="Uguest-b",
        message_text="可以加床嗎",
        created_at=_created_at_from_taipei(2026, 3, 16, 1, 2),
        reply_text=None,
    )
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes(
        [_text_event_with_reply_token("/紀錄", "rt-record", user_id="Uowner-a")]
    )

    response = _post(client, body, _sign(body))

    expected_text = (
        "🌙 今晚紀錄（共 2 則）"
        "\n\n03/15 23:41\n客：請問還有空房嗎\n系統：目前仍有空房"
        f"\n\n03/16 01:02\n客：可以加床嗎\n系統：{OWNER_RECORD_UNREPLIED_TEXT}"
    )
    assert response.status_code == 200
    assert len(replies) == 1
    assert replies[0]["reply_token"] == "rt-record"
    assert replies[0]["text"] == expected_text
    assert pushes == []
    assert "白天訊息不應出現" not in replies[0]["text"]
    assert "owner 自己的訊息不應出現" not in replies[0]["text"]
    assert len(_rows(database_path, "messages")) == 4


def test_owner_record_command_replies_empty_message_when_no_guest_messages(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _freeze_owner_record_now(monkeypatch, hour=10, minute=0)
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes(
        [_text_event_with_reply_token("/紀錄", "rt-record", user_id="Uowner-a")]
    )

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert len(replies) == 1
    assert replies[0]["text"] == f"🌙 今晚紀錄\n\n{OWNER_RECORD_EMPTY_MESSAGE}"
    assert pushes == []
    assert _rows(database_path, "messages") == []


def test_owner_record_command_daytime_excludes_messages_after_window_end(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _freeze_owner_record_now(monkeypatch, hour=10, minute=0)
    _seed_message_at(
        database_path,
        tenant_id=tenant_id,
        user_id="Uguest-before-end",
        message_text="退房前訊息",
        created_at=_created_at_from_taipei(2026, 3, 16, 7, 59),
        reply_text="退房前回覆",
    )
    _seed_message_at(
        database_path,
        tenant_id=tenant_id,
        user_id="Uguest-after-end",
        message_text="出窗後白天訊息不應出現",
        created_at=_created_at_from_taipei(2026, 3, 16, 8, 30),
        reply_text="白天回覆",
    )
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes(
        [_text_event_with_reply_token("/紀錄", "rt-record", user_id="Uowner-a")]
    )

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert len(replies) == 1
    assert "退房前訊息" in replies[0]["text"]
    assert "出窗後白天訊息不應出現" not in replies[0]["text"]
    assert "🌙 今晚紀錄（共 1 則）" in replies[0]["text"]
    assert pushes == []


def test_owner_full_width_record_command_matches(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _freeze_owner_record_now(monkeypatch, hour=10, minute=0)
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes(
        [_text_event_with_reply_token("／紀錄", "rt-record", user_id="Uowner-a")]
    )

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert len(replies) == 1
    assert OWNER_RECORD_EMPTY_MESSAGE in replies[0]["text"]
    assert pushes == []
    assert _rows(database_path, "messages") == []


def test_non_owner_record_command_is_invisible_and_flows_normally(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    OperationModeService(repo=OperationStateRepository(database_path)).turn_off(
        tenant_id=tenant_id, tenant_timezone=_TZ
    )
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, pushes = _capture_sends(monkeypatch, owner_id=None)

    def _unexpected_record_reply(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("non-owner /紀錄 must not call owner-record reply")

    monkeypatch.setattr(line_webhook_routes, "_reply_owner_record", _unexpected_record_reply)
    body = _payload_bytes([_text_event_with_reply_token("/紀錄", user_id="Uguest")])

    response = _post(client, body, _sign(body))

    messages = _rows(database_path, "messages")
    assert response.status_code == 200
    assert replies == []
    assert pushes == []
    assert len(messages) == 1
    assert messages[0]["message_text"] == "/紀錄"


def test_owner_full_width_turn_on_command_matches(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes([_text_event_with_reply_token("／開機", user_id="Uowner-a")])

    response = _post(client, body, _sign(body))

    row = OperationStateRepository(database_path).get_or_create(tenant_id)
    assert response.status_code == 200
    assert row["manual_mode"] == "on"
    assert replies == []
    assert [push["to_user_id"] for push in pushes] == ["Uowner-a"]
    assert pushes[0]["text"] == OWNER_COMMAND_TURN_ON_MESSAGE
    assert _rows(database_path, "messages") == []


def test_slash_text_that_is_not_command_flows_normally(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    OperationModeService(repo=OperationStateRepository(database_path)).turn_off(
        tenant_id=tenant_id, tenant_timezone=_TZ
    )
    replies, pushes = _capture_sends(monkeypatch, owner_id=None)
    body = _payload_bytes([_text_event_with_reply_token("/你好", user_id="Uguest")])

    response = _post(client, body, _sign(body))

    row = OperationStateRepository(database_path).get_or_create(tenant_id)
    assert response.status_code == 200
    assert row["manual_mode"] == "off"
    assert replies == []
    assert pushes == []
    assert len(_rows(database_path, "messages")) == 1


def test_urgent_owner_push_is_sent_to_owner(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes([_text_event_with_reply_token("火災!!")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert replies == []
    assert len(pushes) == 1
    assert pushes[0]["to_user_id"] == "Uowner"
    assert "火災" in pushes[0]["text"]


def test_off_mode_urgent_still_pushes_owner(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    OperationModeService(repo=OperationStateRepository(database_path)).turn_off(
        tenant_id=tenant_id, tenant_timezone=_TZ
    )
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes([_text_event_with_reply_token("火災!!")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert replies == []
    assert len(pushes) == 1
    assert pushes[0]["to_user_id"] == "Uowner"


def test_owner_push_falls_back_to_env_when_tenant_owner_table_empty(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _, pushes = _capture_sends(monkeypatch, owner_id="Uenv-owner")

    sent = line_webhook_routes._send_owner_push(
        database_path=database_path,
        tenant_id=tenant_id,
        text="owner notice",
    )

    assert sent is True
    assert [push["to_user_id"] for push in pushes] == ["Uenv-owner"]


def test_urgent_owner_push_is_sent_to_every_active_tenant_owner(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_tenant_owner(database_path, tenant_id, "Uowner-b")
    replies, pushes = _capture_sends(monkeypatch, owner_id="Ufallback-owner")
    body = _payload_bytes([_text_event_with_reply_token("火災!!")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert replies == []
    assert [push["to_user_id"] for push in pushes] == ["Uowner-a", "Uowner-b"]


def test_send_owner_push_partial_success_returns_true_for_truthful_notified(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-first")
    _seed_tenant_owner(database_path, tenant_id, "Uowner-second")
    _, pushes = _capture_sends(
        monkeypatch,
        push_raises_for={"Uowner-first"},
    )

    sent = line_webhook_routes._send_owner_push(
        database_path=database_path,
        tenant_id=tenant_id,
        text="owner notice",
    )

    assert sent is True
    assert [push["to_user_id"] for push in pushes] == [
        "Uowner-first",
        "Uowner-second",
    ]


def test_owner_push_partial_failure_does_not_interrupt_webhook_or_later_owner(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-first")
    _seed_tenant_owner(database_path, tenant_id, "Uowner-second")
    replies, pushes = _capture_sends(
        monkeypatch,
        push_raises_for={"Uowner-first"},
    )
    body = _payload_bytes([_text_event_with_reply_token("火災!!")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert replies == []
    assert [push["to_user_id"] for push in pushes] == [
        "Uowner-first",
        "Uowner-second",
    ]
    assert len(_rows(database_path, "messages")) == 1


def test_off_mode_non_urgent_does_not_push_owner(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    OperationModeService(repo=OperationStateRepository(database_path)).turn_off(
        tenant_id=tenant_id, tenant_timezone=_TZ
    )
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes([_text_event_with_reply_token("你好")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert replies == []
    assert pushes == []


def test_faq_tier1_breakfast_answers_with_no_push(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes([_text_event_with_reply_token("請問有早餐嗎")])

    assert _post(client, body, _sign(body)).status_code == 200
    assert len(replies) == 1
    assert "沒有提供早餐" in replies[0]["text"]  # real config: breakfast_provided=false
    assert _NOTIFIED not in replies[0]["text"]
    assert pushes == []  # tier-1 self-contained -> no owner push
    assert _rows(database_path, "conversation_states") == []  # FAQ touches no state


def test_faq_tier1_wifi_answers_with_no_push(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes([_text_event_with_reply_token("請問有wifi嗎")])

    assert _post(client, body, _sign(body)).status_code == 200
    assert len(replies) == 1
    assert "免費" in replies[0]["text"]  # real config: wifi_provided=true, wifi_free=true
    assert _NOTIFIED not in replies[0]["text"]
    assert pushes == []  # tier-1 self-contained -> no owner push


def test_faq_tier1_wifi_no_push_even_when_push_infra_unavailable(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch, push_raises=True)
    body = _payload_bytes([_text_event_with_reply_token("請問有wifi嗎")])

    response = _post(client, body, _sign(body))

    # Tier-1 requires no owner push; push infra being down must not affect anything.
    assert response.status_code == 200
    assert pushes == []  # push never attempted
    assert len(replies) == 1
    assert "免費" in replies[0]["text"]
    assert _NOTIFIED not in replies[0]["text"]
    assert len(_rows(database_path, "messages")) == 1  # persistence intact


def test_faq_push_failure_isolated_customer_reply_and_200_intact(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even with NO owner id configured (push impossible), the customer is still
    # answered (softer wording) and the 200 + persistence are untouched.
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch, owner_id=None)
    body = _payload_bytes([_text_event_with_reply_token("附近有什麼好玩的嗎")])  # non-whitelist faq

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert pushes == []  # no owner id -> push skipped (not attempted)
    assert len(replies) == 1
    assert _NOTIFIED not in replies[0]["text"]  # never lie when nothing was sent
    assert len(_rows(database_path, "messages")) == 1


def test_faq_silent_in_off_mode_no_push(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    OperationModeService(repo=OperationStateRepository(database_path)).turn_off(
        tenant_id=tenant_id, tenant_timezone=_TZ
    )
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes([_text_event_with_reply_token("請問有wifi嗎")])

    assert _post(client, body, _sign(body)).status_code == 200
    assert replies == []  # off mode -> silent
    assert pushes == []   # and no owner push
    assert len(_rows(database_path, "messages")) == 1  # still received


# ============================================================
# LAYER 1: per-customer handoff pause ("/<display name>" toggle)
# ============================================================


def _set_display_name(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(
        line_webhook_routes, "get_profile", lambda **kw: {"displayName": name}
    )


def test_handoff_toggle_pauses_then_resumes_by_display_name(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, pushes = _capture_sends(monkeypatch, owner_id=None)
    _set_display_name(monkeypatch, "Wendy")
    guest_body = _payload_bytes([_text_event("5/12 入住 5/13 退房 4 大人 開2房 多少錢?")])
    _post(client, guest_body, _sign(guest_body))
    replies.clear()

    pause_body = _payload_bytes(
        [_text_event_with_reply_token("/Wendy", "rt-1", user_id="Uowner-a")]
    )
    pause_response = _post(client, pause_body, _sign(pause_body))

    assert pause_response.status_code == 200
    assert "已暫停" in replies[0]["text"]
    replies.clear()

    guest_body_2 = _payload_bytes([_text_event_with_reply_token("還在嗎", user_id="Uguest")])
    _post(client, guest_body_2, _sign(guest_body_2))
    assert replies == []  # paused customer: system stays silent even though tenant is "on"

    resume_body = _payload_bytes(
        [_text_event_with_reply_token("/Wendy", "rt-2", user_id="Uowner-a")]
    )
    resume_response = _post(client, resume_body, _sign(resume_body))

    assert resume_response.status_code == 200
    assert "已恢復" in replies[0]["text"]


def test_handoff_toggle_not_found_replies_politely(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, _ = _capture_sends(monkeypatch, owner_id=None)
    body = _payload_bytes(
        [_text_event_with_reply_token("/NoSuchGuest", "rt-1", user_id="Uowner-a")]
    )

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert "查無" in replies[0]["text"]


def test_handoff_toggle_ambiguous_lists_candidates(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, _ = _capture_sends(monkeypatch, owner_id=None)
    _set_display_name(monkeypatch, "Wendy")
    for user_id in ("Uguest-1", "Uguest-2"):
        body = _payload_bytes([_text_event(f"{user_id} 你好嗎", user_id=user_id)])
        _post(client, body, _sign(body))
    replies.clear()

    toggle_body = _payload_bytes(
        [_text_event_with_reply_token("/Wendy", "rt-1", user_id="Uowner-a")]
    )
    response = _post(client, toggle_body, _sign(toggle_body))

    assert response.status_code == 200
    assert "請問是哪一位" in replies[0]["text"]


def test_handoff_toggle_numbered_selection_pauses_correct_candidate(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After an ambiguous "/Wendy" listed candidates 1/2, "/Wendy 1" must
    actually resolve to and pause the first candidate -- previously this was
    parsed as a literal (nonexistent) display name "Wendy 1" and always
    failed with "查無"."""
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, _ = _capture_sends(monkeypatch, owner_id=None)
    _set_display_name(monkeypatch, "Wendy")
    for user_id in ("Uguest-1", "Uguest-2"):
        body = _payload_bytes([_text_event(f"{user_id} 你好嗎", user_id=user_id)])
        _post(client, body, _sign(body))
    ambiguous_body = _payload_bytes(
        [_text_event_with_reply_token("/Wendy", "rt-1", user_id="Uowner-a")]
    )
    _post(client, ambiguous_body, _sign(ambiguous_body))
    replies.clear()
    candidates = ConversationHandoffService(
        hold_repo=ManualHoldRepository(database_path),
        message_repo=MessageRepository(database_path),
        operation_state_repo=OperationStateRepository(database_path),
    ).resolve_by_display_name(tenant_id=tenant_id, platform="line", display_name="Wendy")
    first_candidate_user_id = candidates.candidates[0].platform_user_id

    select_body = _payload_bytes(
        [_text_event_with_reply_token("/Wendy 1", "rt-2", user_id="Uowner-a")]
    )
    response = _post(client, select_body, _sign(select_body))

    assert response.status_code == 200
    assert "已暫停" in replies[0]["text"]
    holds = _rows(database_path, "conversation_manual_holds")
    assert len(holds) == 1
    assert holds[0]["platform_user_id"] == first_candidate_user_id


def test_handoff_toggle_numbered_selection_out_of_range_relists_candidates(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, _ = _capture_sends(monkeypatch, owner_id=None)
    _set_display_name(monkeypatch, "Wendy")
    for user_id in ("Uguest-1", "Uguest-2"):
        body = _payload_bytes([_text_event(f"{user_id} 你好嗎", user_id=user_id)])
        _post(client, body, _sign(body))
    replies.clear()

    select_body = _payload_bytes(
        [_text_event_with_reply_token("/Wendy 9", "rt-2", user_id="Uowner-a")]
    )
    response = _post(client, select_body, _sign(select_body))

    assert response.status_code == 200
    assert "請問是哪一位" in replies[0]["text"]
    assert _rows(database_path, "conversation_manual_holds") == []


def test_handoff_toggle_display_name_ending_in_digits_resolves_literally(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A display name that itself ends in digits (e.g. "Room 101") used to
    always be misparsed as name="Room" + candidate_index=101 (nobody had ever
    shown the owner a numbered list), which always failed with "查無". The
    exact full string must be tried as a literal display name first."""
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, _ = _capture_sends(monkeypatch, owner_id=None)
    _set_display_name(monkeypatch, "Room 101")
    guest_body = _payload_bytes([_text_event("你好", user_id="Uguest-1")])
    _post(client, guest_body, _sign(guest_body))
    replies.clear()

    toggle_body = _payload_bytes(
        [_text_event_with_reply_token("/Room 101", "rt-1", user_id="Uowner-a")]
    )
    response = _post(client, toggle_body, _sign(toggle_body))

    assert response.status_code == 200
    assert "已暫停" in replies[0]["text"]


def test_urgent_message_from_paused_customer_still_pushes_owner(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, pushes = _capture_sends(monkeypatch, owner_id=None)
    _set_display_name(monkeypatch, "Wendy")
    guest_body = _payload_bytes([_text_event("哈囉")])
    _post(client, guest_body, _sign(guest_body))
    toggle_body = _payload_bytes(
        [_text_event_with_reply_token("/Wendy", "rt-1", user_id="Uowner-a")]
    )
    _post(client, toggle_body, _sign(toggle_body))
    pushes.clear()

    urgent_body = _payload_bytes([_text_event_with_reply_token("火災!")])
    _post(client, urgent_body, _sign(urgent_body))

    assert len(pushes) == 1  # urgent bypasses the pause entirely


# ============================================================
# LAYER 3: /待回覆 pull command + nightly digest
# ============================================================


def test_pending_command_lists_unhandled_messages(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_message_at(
        database_path, tenant_id=tenant_id, user_id="Uguest-a",
        message_text="8/15 還有空房嗎", created_at=_created_at_from_taipei(2026, 3, 15, 14, 0),
    )
    replies, _ = _capture_sends(monkeypatch, owner_id=None)
    body = _payload_bytes(
        [_text_event_with_reply_token("/待回覆", "rt-1", user_id="Uowner-a")]
    )

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert "8/15 還有空房嗎" in replies[0]["text"]
    assert OWNER_RECORD_UNREPLIED_TEXT in replies[0]["text"]


def test_pending_command_excludes_paused_by_owner_rows(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_message_at(
        database_path, tenant_id=tenant_id, user_id="Uguest-a",
        message_text="已由主人接手的訊息", created_at=_created_at_from_taipei(2026, 3, 15, 14, 0),
    )
    with closing(get_connection(database_path)) as conn:
        conn.execute("UPDATE messages SET system_state_at_time = 'paused_by_owner'")
        conn.commit()
    replies, _ = _capture_sends(monkeypatch, owner_id=None)
    body = _payload_bytes(
        [_text_event_with_reply_token("/待回覆", "rt-1", user_id="Uowner-a")]
    )

    _post(client, body, _sign(body))

    assert "已由主人接手的訊息" not in replies[0]["text"]


def test_pending_command_empty_when_nothing_unhandled(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    replies, _ = _capture_sends(monkeypatch, owner_id=None)
    body = _payload_bytes(
        [_text_event_with_reply_token("/待回覆", "rt-1", user_id="Uowner-a")]
    )

    _post(client, body, _sign(body))

    assert OWNER_PENDING_EMPTY_MESSAGE in replies[0]["text"]


def test_nightly_digest_pushes_once_when_unhandled_present(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_message_at(
        database_path, tenant_id=tenant_id, user_id="Uguest-a",
        message_text="夜間漏接訊息", created_at=_created_at_from_taipei(2026, 3, 15, 14, 0),
    )
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    _freeze_owner_record_now(monkeypatch, hour=23, minute=5)
    pushes: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "push_message", lambda **kw: pushes.append(kw))

    line_webhook_routes.run_nightly_digest_check(database_path)

    assert len(pushes) == 1
    assert "今晚有 1 則" in pushes[0]["text"]
    row = OperationStateRepository(database_path).get_or_create(tenant_id)
    assert row["last_digest_sent_date"] is not None


def test_nightly_digest_no_push_when_nothing_unhandled(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    _freeze_owner_record_now(monkeypatch, hour=23, minute=5)
    pushes: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "push_message", lambda **kw: pushes.append(kw))

    line_webhook_routes.run_nightly_digest_check(database_path)

    assert pushes == []


def test_pending_command_closes_shown_rows(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_message_at(
        database_path, tenant_id=tenant_id, user_id="Uguest-a",
        message_text="8/15 還有空房嗎", created_at=_created_at_from_taipei(2026, 3, 15, 14, 0),
    )
    replies, _ = _capture_sends(monkeypatch, owner_id=None)
    first_body = _payload_bytes(
        [_text_event_with_reply_token("/待回覆", "rt-1", user_id="Uowner-a")]
    )
    _post(client, first_body, _sign(first_body))
    replies.clear()
    second_body = _payload_bytes(
        [_text_event_with_reply_token("/待回覆", "rt-2", user_id="Uowner-a")]
    )

    _post(client, second_body, _sign(second_body))

    # already shown once via /待回覆 -> closed out, second call finds nothing left
    assert OWNER_PENDING_EMPTY_MESSAGE in replies[0]["text"]


def test_nightly_digest_leaves_rows_pending_after_successful_push(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The digest only pushes a COUNT ("共 N 則,請輸入 /待回覆 查看"), never the
    actual message content -- so a successful digest push must NOT close the
    rows. Only /待回覆 (which actually shows the content) may close them,
    otherwise the owner follows the digest's own instruction and finds an
    empty list."""
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_message_at(
        database_path, tenant_id=tenant_id, user_id="Uguest-a",
        message_text="夜間漏接訊息", created_at=_created_at_from_taipei(2026, 3, 15, 14, 0),
    )
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    _freeze_owner_record_now(monkeypatch, hour=23, minute=5)
    monkeypatch.setattr(line_webhook_routes, "push_message", lambda **kw: None)

    line_webhook_routes.run_nightly_digest_check(database_path)

    messages = _rows(database_path, "messages")
    assert messages[0]["handled"] == 0  # still pending -> visible via /待回覆


def test_nightly_digest_push_failure_leaves_rows_pending(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_message_at(
        database_path, tenant_id=tenant_id, user_id="Uguest-a",
        message_text="夜間漏接訊息(推播失敗)", created_at=_created_at_from_taipei(2026, 3, 15, 14, 0),
    )
    monkeypatch.delenv(_ACCESS_TOKEN_ENV, raising=False)  # no token -> push fails
    _freeze_owner_record_now(monkeypatch, hour=23, minute=5)

    line_webhook_routes.run_nightly_digest_check(database_path)

    messages = _rows(database_path, "messages")
    assert messages[0]["handled"] == 0  # never shown to anyone -> stays pending


# ============================================================
# LAYER 3b: handled reflects actual delivery, not the optimistic action_type
# ============================================================


def test_uncategorized_message_owner_push_failure_stays_unhandled(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """push_to_owner_only with no customer reply and no push_failed_text
    fallback: if the owner push fails, nobody learns about this message. The
    optimistic handled=True the mapper wrote at persist time must be
    corrected back to 0 so it surfaces via /待回覆 instead of vanishing."""
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    monkeypatch.delenv(_ACCESS_TOKEN_ENV, raising=False)  # owner push will fail
    body = _payload_bytes([_text_event("測試訊息abc123不知道在問什麼")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    messages = _rows(database_path, "messages")
    assert len(messages) == 1
    assert messages[0]["handled"] == 0


def test_uncategorized_message_owner_push_success_stays_handled(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    monkeypatch.setattr(line_webhook_routes, "push_message", lambda **kw: None)
    body = _payload_bytes([_text_event("測試訊息abc123不知道在問什麼")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    messages = _rows(database_path, "messages")
    assert messages[0]["handled"] == 1


def test_urgent_message_owner_push_failure_stays_unhandled(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even push_owner_urgent (fire-alarm class messages) has no customer
    reply and no push_failed_text fallback -- a failed push must not be
    silently marked handled=True either."""
    tenant_id = _seed_channel(database_path)
    monkeypatch.delenv(_ACCESS_TOKEN_ENV, raising=False)
    body = _payload_bytes([_text_event("火災!")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    messages = _rows(database_path, "messages")
    assert messages[0]["handled"] == 0


def test_nightly_digest_not_yet_due_before_schedule_start(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_message_at(
        database_path, tenant_id=tenant_id, user_id="Uguest-a",
        message_text="還沒到晚上", created_at=_created_at_from_taipei(2026, 3, 15, 14, 0),
    )
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    _freeze_owner_record_now(monkeypatch, hour=14, minute=0)
    pushes: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "push_message", lambda **kw: pushes.append(kw))

    line_webhook_routes.run_nightly_digest_check(database_path)

    assert pushes == []  # before auto_on_start_time (23:00) -> not due yet


def test_nightly_digest_fires_for_stale_boundary_after_midnight_restart(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the digest-check loop wasn't running through last night's 23:00
    boundary (e.g. a deploy restart) and only comes back up at 00:05, the
    stale boundary must still be caught -- not silently skipped until the
    NEXT day's 23:00. Regression test for the bare now.time() < start_time
    comparison, which can't tell "haven't reached today's boundary yet" from
    "already past yesterday's boundary" once the clock has wrapped."""
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_message_at(
        database_path, tenant_id=tenant_id, user_id="Uguest-a",
        message_text="半夜重啟前漏接的訊息", created_at=_created_at_from_taipei(2026, 3, 15, 14, 0),
    )
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    _freeze_owner_record_now(monkeypatch, day=16, hour=0, minute=5)  # just after midnight
    pushes: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "push_message", lambda **kw: pushes.append(kw))

    line_webhook_routes.run_nightly_digest_check(database_path)

    assert len(pushes) == 1
    row = OperationStateRepository(database_path).get_or_create(tenant_id)
    assert row["last_digest_sent_date"] == "2026-03-15"  # yesterday's boundary, not today's


def test_nightly_digest_daytime_gap_between_windows_not_due(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """08:30 is past both yesterday's and today's on-window ends -- the
    daytime gap between windows must stay "not due", not misread as a stale
    boundary needing catch-up."""
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_message_at(
        database_path, tenant_id=tenant_id, user_id="Uguest-a",
        message_text="白天訊息", created_at=_created_at_from_taipei(2026, 3, 15, 14, 0),
    )
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    _freeze_owner_record_now(monkeypatch, day=16, hour=8, minute=30)
    pushes: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "push_message", lambda **kw: pushes.append(kw))

    line_webhook_routes.run_nightly_digest_check(database_path)

    assert pushes == []


def test_nightly_digest_retries_after_push_failure(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed push must not be marked as sent -- the next 5-minute poll
    tick should retry rather than waiting until tomorrow's boundary."""
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    _seed_message_at(
        database_path, tenant_id=tenant_id, user_id="Uguest-a",
        message_text="推播會失敗的訊息", created_at=_created_at_from_taipei(2026, 3, 15, 14, 0),
    )
    _freeze_owner_record_now(monkeypatch, day=16, hour=23, minute=5)
    monkeypatch.delenv(_ACCESS_TOKEN_ENV, raising=False)  # no token -> push fails

    line_webhook_routes.run_nightly_digest_check(database_path)

    row = OperationStateRepository(database_path).get_or_create(tenant_id)
    assert row["last_digest_sent_date"] is None  # not marked sent -> retryable
    messages = _rows(database_path, "messages")
    assert messages[0]["handled"] == 0  # not shown to anyone yet

    # transient failure recovers on the next poll tick
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    pushes: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "push_message", lambda **kw: pushes.append(kw))

    line_webhook_routes.run_nightly_digest_check(database_path)

    assert len(pushes) == 1
    row = OperationStateRepository(database_path).get_or_create(tenant_id)
    assert row["last_digest_sent_date"] == "2026-03-16"


def test_nightly_digest_only_fires_once_per_tenant_local_day(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    _seed_tenant_owner(database_path, tenant_id, "Uowner-a")
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    _freeze_owner_record_now(monkeypatch, hour=23, minute=5)
    pushes: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "push_message", lambda **kw: pushes.append(kw))
    line_webhook_routes.run_nightly_digest_check(database_path)
    _seed_message_at(
        database_path, tenant_id=tenant_id, user_id="Uguest-a",
        message_text="第二次檢查前才收到的訊息",
        created_at=_created_at_from_taipei(2026, 3, 15, 14, 0),
    )

    line_webhook_routes.run_nightly_digest_check(database_path)

    assert pushes == []  # already marked sent for today; would need /待回覆 or tomorrow


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
        line_webhook_routes._reject,
        line_webhook_routes._parse_payload,
        line_webhook_routes._resolve_channel,
        line_webhook_routes._verify,
        line_webhook_routes._resolve_tenant,
        line_webhook_routes._extract_events,
        line_webhook_routes._build_inquiry_service,
        line_webhook_routes._build_reply_composer,
        line_webhook_routes._event_to_message,
        line_webhook_routes._send_owner_push,
        line_webhook_routes._resolve_customer_text,
        line_webhook_routes._record_state,
        line_webhook_routes._compose_reply,
        line_webhook_routes._mark_if_complete,
        line_webhook_routes._run_pipeline,
    ],
)
def test_methods_under_15_body_lines(func) -> None:
    assert _body_line_count(func) <= 15, (
        f"{func.__qualname__} body too long: {_body_line_count(func)} lines"
    )
