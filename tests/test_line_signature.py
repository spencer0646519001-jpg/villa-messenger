import base64
import hashlib
import hmac
import inspect

import pytest

from app.adapters.line_signature import LineSignatureError, verify_signature


CHANNEL_SECRET = "test-channel-secret-not-a-real-credential"


def _sign(body: bytes, secret: str = CHANNEL_SECRET) -> str:
    """Self-contained: compute the same HMAC-SHA256+base64 LINE uses, so the
    test proves the algorithm rather than checking a frozen base64 blob."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


# ============================================================
# VALID SIGNATURE
# ============================================================


def test_valid_signature_does_not_raise() -> None:
    body = b'{"destination":"Uabc","events":[]}'
    sig = _sign(body)

    verify_signature(request_body=body, x_line_signature=sig, channel_secret=CHANNEL_SECRET)


def test_valid_signature_over_realistic_payload() -> None:
    body = b'{"destination":"Uabc","events":[{"type":"message","message":{"type":"text","text":"hi"}}]}'
    sig = _sign(body)

    verify_signature(request_body=body, x_line_signature=sig, channel_secret=CHANNEL_SECRET)


# ============================================================
# MISSING / EMPTY SIGNATURE
# ============================================================


def test_none_signature_raises() -> None:
    body = b'{"events":[]}'

    with pytest.raises(LineSignatureError):
        verify_signature(request_body=body, x_line_signature=None, channel_secret=CHANNEL_SECRET)


def test_empty_string_signature_raises() -> None:
    body = b'{"events":[]}'

    with pytest.raises(LineSignatureError):
        verify_signature(request_body=body, x_line_signature="", channel_secret=CHANNEL_SECRET)


# ============================================================
# INVALID / TAMPERED
# ============================================================


def test_garbage_signature_raises() -> None:
    body = b'{"events":[]}'

    with pytest.raises(LineSignatureError):
        verify_signature(request_body=body, x_line_signature="not-a-real-sig", channel_secret=CHANNEL_SECRET)


def test_tampered_body_raises() -> None:
    original_body = b'{"events":[{"type":"message","message":{"type":"text","text":"hello"}}]}'
    tampered_body = b'{"events":[{"type":"message","message":{"type":"text","text":"evil"}}]}'
    sig_for_original = _sign(original_body)

    with pytest.raises(LineSignatureError):
        verify_signature(
            request_body=tampered_body,
            x_line_signature=sig_for_original,
            channel_secret=CHANNEL_SECRET,
        )


def test_signature_computed_with_wrong_secret_raises() -> None:
    body = b'{"events":[]}'
    sig_with_other_secret = _sign(body, secret="some-other-secret")

    with pytest.raises(LineSignatureError):
        verify_signature(
            request_body=body,
            x_line_signature=sig_with_other_secret,
            channel_secret=CHANNEL_SECRET,
        )


# ============================================================
# METHOD-LENGTH DISCIPLINE
# ============================================================


def _body_line_count(func) -> int:
    src = inspect.getsource(func)
    lines = [line for line in src.splitlines()[1:] if line.strip() and not line.strip().startswith("#")]
    return len(lines)


@pytest.mark.parametrize("func", [verify_signature])
def test_methods_under_15_body_lines(func) -> None:
    assert _body_line_count(func) <= 15, (
        f"{func.__qualname__} body too long: {_body_line_count(func)} lines"
    )
