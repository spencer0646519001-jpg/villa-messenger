from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app.domain.llm_provider import LLMProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _BuiltProvider:
    provider: LLMProvider
    provider_name: str
    model: str


def build_llm_provider_from_env() -> LLMProvider | None:
    if not _env_bool("LLM_ENABLED", default=False):
        return None

    provider_name = os.environ.get("LLM_PROVIDER", "deepseek").strip().lower()
    timeout_s = _env_float("LLM_TIMEOUT_SECONDS", default=5)
    primary = _build_provider(
        provider_name=provider_name,
        provider_env_name="LLM_PROVIDER",
        model_env_name="LLM_PRIMARY_MODEL",
        timeout_s=timeout_s,
    )
    if primary is None:
        return None

    if not _env_bool("LLM_FALLBACK_ENABLED", default=False):
        return primary.provider

    fallback_provider_name = os.environ.get("LLM_FALLBACK_PROVIDER", "openai").strip().lower()
    fallback_timeout_s = _env_float("LLM_FALLBACK_TIMEOUT_SECONDS", default=8)
    fallback = _build_provider(
        provider_name=fallback_provider_name,
        provider_env_name="LLM_FALLBACK_PROVIDER",
        model_env_name="LLM_FALLBACK_MODEL",
        timeout_s=fallback_timeout_s,
    )
    if fallback is None:
        logger.warning(
            "LLM_FALLBACK_ENABLED=true but fallback provider could not be built; "
            "using primary provider only"
        )
        return primary.provider

    from app.adapters.llm.fallback_provider import FallbackLLMProvider, LLMProviderIdentity

    return FallbackLLMProvider(
        primary_provider=primary.provider,
        fallback_provider=fallback.provider,
        primary_identity=LLMProviderIdentity(
            provider=primary.provider_name,
            model=primary.model,
        ),
        fallback_identity=LLMProviderIdentity(
            provider=fallback.provider_name,
            model=fallback.model,
        ),
    )


def _build_provider(
    *,
    provider_name: str,
    provider_env_name: str,
    model_env_name: str,
    timeout_s: float,
) -> _BuiltProvider | None:
    if provider_name == "fake":
        from app.adapters.llm.fake_provider import FakeProvider

        model = os.environ.get(model_env_name, "fake")
        return _BuiltProvider(
            provider=FakeProvider(),
            provider_name=provider_name,
            model=model,
        )

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("%s=%s requires OPENROUTER_API_KEY but it is not set", provider_env_name, provider_name)
        return None
    return _openrouter_provider(
        provider_name=provider_name,
        provider_env_name=provider_env_name,
        model_env_name=model_env_name,
        api_key=api_key,
        timeout_s=timeout_s,
    )


def _openrouter_provider(
    provider_name: str,
    provider_env_name: str,
    model_env_name: str,
    api_key: str,
    timeout_s: float,
) -> _BuiltProvider | None:
    if provider_name == "deepseek":
        from app.adapters.llm.deepseek_provider import DEFAULT_DEEPSEEK_MODEL, DeepSeekProvider

        model = os.environ.get(model_env_name, DEFAULT_DEEPSEEK_MODEL)
        return _BuiltProvider(
            provider=DeepSeekProvider(api_key=api_key, model=model, timeout_s=timeout_s),
            provider_name=provider_name,
            model=model,
        )
    if provider_name == "openai":
        from app.adapters.llm.openai_provider import DEFAULT_OPENAI_MODEL, OpenAIProvider

        model = os.environ.get(model_env_name, DEFAULT_OPENAI_MODEL)
        return _BuiltProvider(
            provider=OpenAIProvider(api_key=api_key, model=model, timeout_s=timeout_s),
            provider_name=provider_name,
            model=model,
        )
    logger.warning("Unknown %s=%s; LLM provider disabled", provider_env_name, provider_name)
    return None


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, *, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %s", name, value, default)
        return default
