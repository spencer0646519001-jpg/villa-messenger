"""
LINE send client: posts messages back to LINE via the Messaging API. This is
the outbound I/O boundary for LINE -- the single place that talks to
api.line.me. It only sends; it does not decide what to send and it does not
swallow failures (callers on the webhook path catch + log so a send error never
breaks receiving).

Two endpoints:
  - reply_message -> /message/reply: answer a specific inbound event (needs a
    replyToken; free, but one-shot and time-limited).
  - push_message  -> /message/push: send unprompted to a known userId (needs the
    recipient's `U...` id; used to notify the OWNER, who has no replyToken).
Both use the same channel access token.
"""

import httpx

_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
_PUSH_URL = "https://api.line.me/v2/bot/message/push"
_TIMEOUT_SECONDS = 5.0


def reply_message(*, reply_token: str, text: str, access_token: str) -> None:
    """POST a single text reply to LINE's reply endpoint.

    Raises httpx.HTTPError (transport/timeout) or httpx.HTTPStatusError
    (non-2xx response). Callers in the webhook path MUST catch and swallow
    these so a send failure never breaks receiving + persistence."""
    response = httpx.post(
        _REPLY_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def push_message(*, to_user_id: str, text: str, access_token: str) -> None:
    """POST a single text push to LINE's push endpoint (to a known userId).

    Same failure contract as reply_message: raises httpx.HTTPError /
    HTTPStatusError; the webhook caller MUST catch + swallow so an owner-push
    failure never breaks the customer reply, persistence, or the 200."""
    response = httpx.post(
        _PUSH_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={"to": to_user_id, "messages": [{"type": "text", "text": text}]},
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
