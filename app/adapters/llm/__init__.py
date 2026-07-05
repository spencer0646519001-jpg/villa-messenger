from __future__ import annotations

import logging
import os

from app.domain.llm_provider import LLMProvider

logger = logging.getLogger(__name__)


def build_llm_provider_from_env() -> LLMProvider | None:
    if not _env_bool("LLM_ENABLED", default=False):
        return None

    provider_name = os.environ.get("LLM_PROVIDER", "deepseek").strip().lower()
    timeout_s = _env_float("LLM_TIMEOUT_SECONDS", default=5)
    if provider_name == "fake":
        from app.adapters.llm.fake_provider import FakeProvider

        return FakeProvider()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("LLM_ENABLED=true but OPENROUTER_API_KEY is not set")
        return None
    return _openrouter_provider(provider_name, api_key, timeout_s)


def _openrouter_provider(
    provider_name: str,
    api_key: str,
    timeout_s: float,
) -> LLMProvider | None:
    if provider_name == "deepseek":
        from app.adapters.llm.deepseek_provider import DEFAULT_DEEPSEEK_MODEL, DeepSeekProvider

        model = os.environ.get("LLM_PRIMARY_MODEL", DEFAULT_DEEPSEEK_MODEL)
        return DeepSeekProvider(api_key=api_key, model=model, timeout_s=timeout_s)
    if provider_name == "openai":
        from app.adapters.llm.openai_provider import DEFAULT_OPENAI_MODEL, OpenAIProvider

        model = os.environ.get("LLM_PRIMARY_MODEL", DEFAULT_OPENAI_MODEL)
        return OpenAIProvider(api_key=api_key, model=model, timeout_s=timeout_s)
    logger.warning("Unknown LLM_PROVIDER=%s; LLM fallback disabled", provider_name)
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
