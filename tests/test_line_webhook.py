import base64
import hashlib
import hmac
import inspect
import json
import shutil
import uuid
from collections.abc import Iterator
from datetime import date
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import line_webhook_routes
from app.api.dependencies import get_database_path
from app.domain.pricing_policy import calculate_price
from app.domain.reply_templates import render_quote_message
from app.domain.reply_text import (
    SINGLE_MISSING_CHECKOUT_MESSAGE,
    SINGLE_MISSING_GUEST_COUNT_MESSAGE,
)
from app.main import app
from app.repositories.conversation_state_repository import ConversationStateRepository
from app.repositories.operation_state_repository import OperationStateRepository
from app.repositories.sqlite import get_connection, init_db
from app.repositories.tenant_channel_repository import TenantChannelRepository
from app.repositories.tenant_repository import TenantRepository
from app.services.conversation_state_service import ConversationStateService
from app.services.operation_mode_service import OperationModeService
from app.services.tenant_config_loaders import (
    make_tenant_pricing_loader,
    make_tenant_special_dates_loader,
)


_SECRET_REF = "LINE_TEST_CHANNEL_SECRET"
_SECRET = "test-channel-secret-value"
_DESTINATION = "Udest123"
_TZ = "Asia/Taipei"


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


def _text_event(text: str) -> dict:
    return {
        "type": "message",
        "timestamp": 1700000000000,
        "source": {"type": "user", "userId": "Uguest"},
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


# ============================================================
# CASE 1: valid signature + quote message -> 200 + rows persisted
# ============================================================


def test_valid_quote_persists_message_and_inquiry(client: TestClient, database_path: Path) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    body = _payload_bytes([_text_event("5/12 入住 5/13 退房 4 大人 多少錢?")])

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    messages = _rows(database_path, "messages")
    inquiries = _rows(database_path, "inquiries")
    assert len(messages) == 1
    assert len(inquiries) == 1
    # Real config-backed loader ran end-to-end: a concrete quote was produced.
    assert inquiries[0]["estimated_total_price"] is not None


# ============================================================
# CASE 2: bad signature -> 400, nothing persisted
# ============================================================


def test_bad_signature_rejected_and_nothing_persisted(client: TestClient, database_path: Path) -> None:
    _seed_channel(database_path)
    body = _payload_bytes([_text_event("5/12 入住 5/13 退房 4 大人 多少錢?")])

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


def _text_event_with_reply_token(text: str, reply_token: str = "rtok-123") -> dict:
    event = _text_event(text)
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


def test_send_failure_swallowed_still_200_and_persisted(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")

    def _boom(**_kw: object) -> None:
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(line_webhook_routes, "reply_message", _boom)
    body = _payload_bytes([_text_event_with_reply_token(_MISSING_INFO_TEXT)])

    response = _post(client, body, _sign(body))

    # Send blew up, but receiving + persistence are untouched and we still ack.
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(_rows(database_path, "messages")) == 1


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
    database_path: Path, tenant_id: int, *, checkin: str, checkout: str, adults: int
) -> str:
    """The quote the single-message path would produce for these slots, built
    from the SAME domain functions + config loaders the route uses. Guards
    against STAGE C growing a divergent quote computation."""
    kwargs = dict(
        checkin_date=date.fromisoformat(checkin),
        checkout_date=date.fromisoformat(checkout),
        adult_count=adults,
        child_count=0,
        infant_count=0,
        pet_count=0,
    )
    pricing = calculate_price(
        **kwargs,
        tenant_pricing=make_tenant_pricing_loader(database_path)(tenant_id),
        tenant_special_dates=make_tenant_special_dates_loader(database_path)(tenant_id),
    )
    return render_quote_message(pricing=pricing, **kwargs)


def _capture_replies(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, "tok-abc")
    calls: list[dict] = []
    monkeypatch.setattr(line_webhook_routes, "reply_message", lambda **kw: calls.append(kw))
    return calls


def test_two_message_complete_flow_quotes_from_accumulation_and_completes(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    calls = _capture_replies(monkeypatch)

    body1 = _payload_bytes([_text_event_with_reply_token(_DATES_PRICE_NO_GUESTS)])
    assert _post(client, body1, _sign(body1)).status_code == 200
    body2 = _payload_bytes([_text_event_with_reply_token("4 大人")])  # completes it
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
    complete = "5/12 入住 5/13 退房 4 大人 多少錢?"
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
    body = _payload_bytes([_text_event_with_reply_token("5/12 入住 5/13 退房 4 大人 多少錢?")])
    assert _post(client, body, _sign(body)).status_code == 200

    assert len(calls) == 1
    assert calls[0]["text"] == _expected_quote(
        database_path, tenant_id, checkin="2026-05-12", checkout="2026-05-13", adults=4
    )
    states = _rows(database_path, "conversation_states")
    assert len(states) == 1
    assert states[0]["status"] == "completed"


def test_mark_completed_failure_isolated_reply_still_sent(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    calls = _capture_replies(monkeypatch)

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("mark_completed exploded")

    monkeypatch.setattr(ConversationStateService, "mark_completed", _boom)
    body = _payload_bytes([_text_event_with_reply_token("5/12 入住 5/13 退房 4 大人 多少錢?")])

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
    monkeypatch: pytest.MonkeyPatch, *, push_raises: bool = False, owner_id: str | None = "Uowner"
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
        if push_raises:
            raise httpx.ConnectError("push network down")

    monkeypatch.setattr(line_webhook_routes, "push_message", _push)
    return replies, pushes


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


def test_faq_tier2_wifi_pushes_owner_then_claims_notified(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch)
    body = _payload_bytes([_text_event_with_reply_token("請問有wifi嗎")])

    assert _post(client, body, _sign(body)).status_code == 200
    # Owner push fired to the configured owner id...
    assert len(pushes) == 1
    assert pushes[0]["to_user_id"] == "Uowner"
    assert pushes[0]["access_token"] == "tok-abc"
    # ...and only then did the customer get the truthful "已通知" wording.
    assert len(replies) == 1
    assert _NOTIFIED in replies[0]["text"]


def test_faq_tier2_push_failure_uses_softer_wording_still_200(
    client: TestClient, database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _seed_channel(database_path)
    _set_system_on(database_path, tenant_id)
    replies, pushes = _capture_sends(monkeypatch, push_raises=True)
    body = _payload_bytes([_text_event_with_reply_token("請問有wifi嗎")])

    response = _post(client, body, _sign(body))

    # Push was attempted but failed -> customer reply must NOT claim "已通知".
    assert response.status_code == 200
    assert len(pushes) == 1
    assert len(replies) == 1
    assert _NOTIFIED not in replies[0]["text"]
    assert "會再請服務人員" in replies[0]["text"]  # softer non-asserting line
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
