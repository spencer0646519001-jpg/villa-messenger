"""
LINE send client: posts a reply back to LINE via the Messaging API reply
endpoint. This is the outbound I/O boundary for LINE -- the single place that
talks to api.line.me. It only sends; it does not decide what to send and it
does not swallow failures (callers on the webhook path catch + log so a send
error never breaks receiving).
"""

import httpx

_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
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
