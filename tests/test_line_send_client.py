"""
Unit tests for the LINE send client (outbound reply boundary).

httpx is mocked -- these tests make NO real network calls. They pin the URL,
auth header, payload shape, and timeout, and confirm the client surfaces a
non-2xx response as an exception (the webhook layer is what swallows it).
"""

import httpx
import pytest

from app.clients import line_send_client
from app.clients.line_send_client import reply_message


class _FakeResponse:
    def __init__(self, *, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=httpx.Request("POST", line_send_client._REPLY_URL),
                response=httpx.Response(self.status_code),
            )


class _Recorder:
    """Captures the single httpx.post call so we can assert its shape."""

    def __init__(self, *, status_code: int = 200, raises: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._status_code = status_code
        self._raises = raises

    def __call__(self, url, *, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if self._raises is not None:
            raise self._raises
        return _FakeResponse(status_code=self._status_code)


def test_posts_to_reply_url_with_bearer_and_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(line_send_client.httpx, "post", recorder)

    reply_message(reply_token="rtok", text="hello", access_token="secrettoken")

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["url"] == "https://api.line.me/v2/bot/message/reply"
    assert call["headers"] == {"Authorization": "Bearer secrettoken"}
    assert call["json"] == {
        "replyToken": "rtok",
        "messages": [{"type": "text", "text": "hello"}],
    }


def test_sends_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(line_send_client.httpx, "post", recorder)

    reply_message(reply_token="rtok", text="hi", access_token="t")

    assert recorder.calls[0]["timeout"] == line_send_client._TIMEOUT_SECONDS
    assert recorder.calls[0]["timeout"] is not None


def test_non_2xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(line_send_client.httpx, "post", _Recorder(status_code=401))

    # The client itself does NOT swallow -- it surfaces the error so the
    # webhook layer can log + swallow it. (See test_line_webhook for the swallow.)
    with pytest.raises(httpx.HTTPStatusError):
        reply_message(reply_token="rtok", text="hi", access_token="bad")


def test_transport_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    boom = httpx.ConnectError("network down")
    monkeypatch.setattr(line_send_client.httpx, "post", _Recorder(raises=boom))

    with pytest.raises(httpx.HTTPError):
        reply_message(reply_token="rtok", text="hi", access_token="t")
