"""
LINE webhook signature verification.

LINE signs each webhook POST with HMAC-SHA256 over the raw request body,
keyed by the channel secret, base64-encoded, sent in the X-Line-Signature
header. This module verifies that signature so we can trust the request
genuinely came from LINE.

Pure function -- takes the secret as an argument. Where the secret comes from
(env var, tenant_channels table) is PR9b's wiring concern.
"""

import base64
import hashlib
import hmac


class LineSignatureError(Exception):
    """Raised when a webhook signature is missing or invalid."""


def verify_signature(*, request_body: bytes, x_line_signature: str | None, channel_secret: str) -> None:
    """Verify LINE's X-Line-Signature header. Raises LineSignatureError on missing/mismatch; returns None on success."""
    if not x_line_signature:
        raise LineSignatureError("missing X-Line-Signature header")
    digest = hmac.new(channel_secret.encode(), request_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    # compare_digest runs in constant time over the input length, so an
    # attacker probing signatures cannot infer how many leading bytes matched
    # from the response latency. Plain `==` short-circuits and leaks that.
    if not hmac.compare_digest(expected, x_line_signature):
        raise LineSignatureError("invalid X-Line-Signature")
