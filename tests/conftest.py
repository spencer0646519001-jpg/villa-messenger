"""Test-wide LINE outbound safety guards.

This file is imported before test modules. Keep the fake env injection at module
import time so app.main's load_dotenv(..., override=False) cannot hydrate real
LINE credentials during pytest.

Known tests currently rely on the global route fake rather than a local mock:
- test_urgent_message_persists_as_urgent
- test_non_inquiry_creates_no_state
TODO: add test-local outbound mocks if these tests start asserting send behavior.
"""

from __future__ import annotations

import os
from typing import Any

import pytest


_ACCESS_TOKEN_ENV = "LINE_TEST_CHANNEL_ACCESS_TOKEN"
_OWNER_USER_ID_ENV = "LINE_TEST_OWNER_USER_ID"
_FAKE_ACCESS_TOKEN = "test-token-do-not-use"
_FAKE_OWNER_USER_ID = "Utest-owner"

# Import-time guard: app.main imports load_dotenv during test module import, and
# load_dotenv uses override=False, so these fake values win over any local .env.
os.environ[_ACCESS_TOKEN_ENV] = _FAKE_ACCESS_TOKEN
os.environ[_OWNER_USER_ID_ENV] = _FAKE_OWNER_USER_ID

ROUTE_OUTBOUND_CALLS: list[dict[str, Any]] = []
ROUTE_REPLY_CALLS: list[dict[str, Any]] = []
ROUTE_PUSH_CALLS: list[dict[str, Any]] = []


@pytest.fixture(autouse=True)
def _isolate_line_outbound(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Keep LINE credentials fake and prevent tests from reaching real LINE I/O."""
    monkeypatch.setenv(_ACCESS_TOKEN_ENV, _FAKE_ACCESS_TOKEN)
    monkeypatch.setenv(_OWNER_USER_ID_ENV, _FAKE_OWNER_USER_ID)

    from app.api import line_webhook_routes
    from app.clients import line_send_client

    # Default webhook-level tests to no LLM provider, regardless of the
    # developer's ambient .env (LLM_ENABLED=true + a real OPENROUTER_API_KEY,
    # needed for live LINE testing of TYPE_1-6). Forcing the LLM_ENABLED env
    # var itself was tried first and reverted: llm_fallback.py's own
    # _llm_enabled() reads that SAME var but defaults to true when unset --
    # test_llm_fallback.py's tests call llm_fallback_parse() directly (with
    # enabled=None) and rely on that default to exercise the FakeProvider
    # they pass in, so forcing the env var off broke ~20 of them. Patching
    # build_llm_provider_from_env() itself (its only two call sites are both
    # in line_webhook_routes.py) affects only the webhook path and leaves
    # direct llm_fallback_parse() calls untouched. A test that needs a real
    # provider here already overrides this locally (many already do), which
    # simply re-monkeypatches over this default for that test's duration.
    monkeypatch.setattr(line_webhook_routes, "build_llm_provider_from_env", lambda: None)

    def _record_route_call(kind: str, kwargs: dict[str, Any]) -> None:
        call = {"test": request.node.nodeid, "kind": kind, "kwargs": dict(kwargs)}
        ROUTE_OUTBOUND_CALLS.append(call)
        if kind == "reply_message":
            ROUTE_REPLY_CALLS.append(call)
        elif kind == "push_message":
            ROUTE_PUSH_CALLS.append(call)

    def _fake_reply_message(**kwargs: Any) -> None:
        _record_route_call("reply_message", kwargs)

    def _fake_push_message(**kwargs: Any) -> None:
        _record_route_call("push_message", kwargs)

    def _fake_get_profile(**kwargs: Any) -> dict[str, Any]:
        # Safe default: no display name (matches pre-existing behavior, where
        # the adapter always set customer_display_name=None). Tests that need
        # a specific name locally monkeypatch line_webhook_routes.get_profile.
        _record_route_call("get_profile", kwargs)
        return {}

    def _blocked_httpx_post(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "Unexpected outbound HTTP via httpx.post during tests. "
            "Patch the route-level LINE send function or the send-client boundary explicitly."
        )

    def _blocked_httpx_get(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "Unexpected outbound HTTP via httpx.get during tests. "
            "Patch the route-level LINE send function or the send-client boundary explicitly."
        )

    monkeypatch.setattr(line_webhook_routes, "reply_message", _fake_reply_message)
    monkeypatch.setattr(line_webhook_routes, "push_message", _fake_push_message)
    monkeypatch.setattr(line_webhook_routes, "get_profile", _fake_get_profile)
    monkeypatch.setattr(line_send_client.httpx, "post", _blocked_httpx_post)
    monkeypatch.setattr(line_send_client.httpx, "get", _blocked_httpx_get)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if ROUTE_OUTBOUND_CALLS:
        print(
            "[tests/conftest.py] LINE route outbound guard intercepted "
            f"{len(ROUTE_OUTBOUND_CALLS)} call(s): "
            f"{len(ROUTE_REPLY_CALLS)} reply, {len(ROUTE_PUSH_CALLS)} push."
        )
