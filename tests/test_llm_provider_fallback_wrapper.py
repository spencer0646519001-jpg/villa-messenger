import logging
import time

import pytest

from app.adapters.llm import build_llm_provider_from_env, openrouter_base
from app.adapters.llm.deepseek_provider import DeepSeekProvider
from app.adapters.llm.fallback_provider import FallbackLLMProvider, LLMProviderIdentity
from app.adapters.llm.openai_provider import OpenAIProvider
from app.domain.inquiry_parser import parse_inquiry
from app.domain.llm_fallback import llm_fallback_parse
from app.domain.llm_provider import (
    LLMFallbackExhaustedError,
    LLMHTTPError,
    LLMOutput,
    LLMParseError,
    LLMProviderError,
    LLMTimeoutError,
)


def _out(**overrides: object) -> LLMOutput:
    values = {
        "intent": None,
        "checkin_date": None,
        "checkout_date": None,
        "adult_count": None,
        "child_count": None,
        "infant_count": None,
        "pet_count": None,
        "has_pet": None,
        "last_message_text": None,
        "is_booking_intent": None,
        "needs_clarification": False,
        "clarification_reason": None,
    }
    values.update(overrides)
    return LLMOutput(**values)


class _StrictFakeProvider:
    def __init__(
        self,
        *,
        output: LLMOutput | None = None,
        error: LLMProviderError | None = None,
    ) -> None:
        self._output = output
        self._error = error
        self.calls: list[dict[str, object]] = []

    def parse_strict(
        self,
        *,
        raw_text: str,
        reference_year: int,
        trigger: str,
        tenant_id: int,
    ) -> LLMOutput:
        self.calls.append(
            {
                "raw_text": raw_text,
                "reference_year": reference_year,
                "trigger": trigger,
                "tenant_id": tenant_id,
            }
        )
        if self._error is not None:
            raise self._error
        assert self._output is not None
        return self._output


def _wrapper(
    primary,
    fallback,
    *,
    primary_model: str = "deepseek/test-primary",
    fallback_model: str = "openai/test-fallback",
) -> FallbackLLMProvider:
    return FallbackLLMProvider(
        primary_provider=primary,
        fallback_provider=fallback,
        primary_identity=LLMProviderIdentity(provider="deepseek", model=primary_model),
        fallback_identity=LLMProviderIdentity(provider="openai", model=fallback_model),
    )


def _parse(provider: FallbackLLMProvider) -> LLMOutput | None:
    return provider.parse(
        raw_text="7/28-29多少錢",
        reference_year=2026,
        trigger="type_1_date_translation",
        tenant_id=1,
    )


def test_primary_success_does_not_call_fallback() -> None:
    primary_output = _out(checkin_date="2026-07-28", checkout_date="2026-07-29")
    primary = _StrictFakeProvider(output=primary_output)
    fallback = _StrictFakeProvider(output=_out(checkin_date="2026-08-01"))

    result = _parse(_wrapper(primary, fallback))

    assert result is primary_output
    assert len(primary.calls) == 1
    assert fallback.calls == []


def test_primary_timeout_falls_back_successfully(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="app.adapters.llm.fallback_provider")
    fallback_output = _out(checkin_date="2026-07-28", checkout_date="2026-07-29")
    primary = _StrictFakeProvider(error=LLMTimeoutError("timed out"))
    fallback = _StrictFakeProvider(output=fallback_output)

    result = _parse(_wrapper(primary, fallback))

    assert result is fallback_output
    assert len(fallback.calls) == 1
    assert "provider=deepseek model=deepseek/test-primary failed reason=timeout" in caplog.text
    assert "provider=openai model=openai/test-fallback final_status=succeeded" in caplog.text


def test_primary_http_error_switches_to_fallback_without_waiting() -> None:
    fallback_output = _out(intent="price")
    primary = _StrictFakeProvider(error=LLMHTTPError("transport failed"))
    fallback = _StrictFakeProvider(output=fallback_output)

    start = time.perf_counter()
    result = _parse(_wrapper(primary, fallback))
    elapsed = time.perf_counter() - start

    assert result is fallback_output
    assert elapsed < 0.25
    assert len(fallback.calls) == 1


def test_primary_bad_json_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openrouter_base, "call_openrouter_strict", lambda **kwargs: "{bad json")
    primary = DeepSeekProvider(api_key="test-key", model="deepseek/test-primary")
    fallback_output = _out(checkin_date="2026-07-28", checkout_date="2026-07-29")
    fallback = _StrictFakeProvider(output=fallback_output)

    result = _parse(_wrapper(primary, fallback))

    assert result is fallback_output
    assert len(fallback.calls) == 1


def test_primary_failure_and_fallback_failure_raise_exhausted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.adapters.llm.fallback_provider")
    primary = _StrictFakeProvider(error=LLMTimeoutError("timed out"))
    fallback = _StrictFakeProvider(error=LLMHTTPError("status 500"))

    with pytest.raises(LLMFallbackExhaustedError) as exc_info:
        _parse(_wrapper(primary, fallback))

    assert exc_info.value.primary_error.reason == "timeout"
    assert exc_info.value.fallback_error.reason == "http_error"
    assert "final_status=failed fallback_reason=http_error" in caplog.text


def test_llm_fallback_parse_catches_exhausted_and_returns_rule_result() -> None:
    primary = _StrictFakeProvider(error=LLMTimeoutError("timed out"))
    fallback = _StrictFakeProvider(error=LLMHTTPError("status 500"))
    provider = _wrapper(primary, fallback)
    inquiry = parse_inquiry("7/28-29多少錢", reference_year=2026)

    result = llm_fallback_parse(
        inquiry,
        "7/28-29多少錢",
        reference_year=2026,
        is_quote_relevant=inquiry.intent.is_inquiry,
        tenant_id=1,
        provider=provider,
    )

    assert result is inquiry


def test_factory_fallback_disabled_returns_primary_without_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "deepseek/test-primary")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "openai/test-fallback")
    monkeypatch.setenv("LLM_FALLBACK_TIMEOUT_SECONDS", "8")

    provider = build_llm_provider_from_env()

    assert isinstance(provider, DeepSeekProvider)
    assert not isinstance(provider, FallbackLLMProvider)
    assert provider._model == "deepseek/test-primary"
    assert provider._timeout_s == 12


def test_factory_fallback_enabled_wraps_primary_and_fallback_with_separate_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "deepseek/test-primary")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("LLM_FALLBACK_TIMEOUT_SECONDS", "8")

    provider = build_llm_provider_from_env()

    assert isinstance(provider, FallbackLLMProvider)
    assert isinstance(provider.primary_provider, DeepSeekProvider)
    assert isinstance(provider.fallback_provider, OpenAIProvider)
    assert provider.primary_identity.provider == "deepseek"
    assert provider.primary_identity.model == "deepseek/test-primary"
    assert provider.fallback_identity.provider == "openai"
    assert provider.fallback_identity.model == "openai/gpt-4o-mini"
    assert provider.primary_provider._timeout_s == 12
    assert provider.fallback_provider._timeout_s == 8
