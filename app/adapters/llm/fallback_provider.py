from __future__ import annotations

import logging
from dataclasses import dataclass

from app.domain.llm_provider import (
    LLMFallbackExhaustedError,
    LLMHTTPError,
    LLMOutput,
    LLMParseError,
    LLMProvider,
    LLMProviderError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMProviderIdentity:
    provider: str
    model: str


class FallbackLLMProvider:
    def __init__(
        self,
        *,
        primary_provider: LLMProvider,
        fallback_provider: LLMProvider,
        primary_identity: LLMProviderIdentity,
        fallback_identity: LLMProviderIdentity,
    ) -> None:
        self._primary_provider = primary_provider
        self._fallback_provider = fallback_provider
        self.primary_identity = primary_identity
        self.fallback_identity = fallback_identity

    @property
    def primary_provider(self) -> LLMProvider:
        return self._primary_provider

    @property
    def fallback_provider(self) -> LLMProvider:
        return self._fallback_provider

    def parse(
        self,
        *,
        raw_text: str,
        reference_year: int,
        trigger: str,
        tenant_id: int,
    ) -> LLMOutput | None:
        primary_error: LLMProviderError | None = None
        try:
            return _parse_with_classified_failure(
                self._primary_provider,
                raw_text=raw_text,
                reference_year=reference_year,
                trigger=trigger,
                tenant_id=tenant_id,
            )
        except LLMProviderError as exc:
            primary_error = exc

        assert primary_error is not None
        try:
            fallback_result = _parse_with_classified_failure(
                self._fallback_provider,
                raw_text=raw_text,
                reference_year=reference_year,
                trigger=trigger,
                tenant_id=tenant_id,
            )
        except LLMProviderError as fallback_error:
            logger.warning(
                "LLM fallback triggered: primary provider=%s model=%s failed "
                "reason=%s; fallback provider=%s model=%s final_status=failed "
                "fallback_reason=%s",
                self.primary_identity.provider,
                self.primary_identity.model,
                primary_error.reason,
                self.fallback_identity.provider,
                self.fallback_identity.model,
                fallback_error.reason,
            )
            raise LLMFallbackExhaustedError(
                "LLM primary and fallback providers both failed",
                primary_error=primary_error,
                fallback_error=fallback_error,
            ) from fallback_error

        logger.warning(
            "LLM fallback triggered: primary provider=%s model=%s failed "
            "reason=%s; fallback provider=%s model=%s final_status=succeeded",
            self.primary_identity.provider,
            self.primary_identity.model,
            primary_error.reason,
            self.fallback_identity.provider,
            self.fallback_identity.model,
        )
        return fallback_result


def _parse_with_classified_failure(
    provider: LLMProvider,
    *,
    raw_text: str,
    reference_year: int,
    trigger: str,
    tenant_id: int,
) -> LLMOutput:
    try:
        strict_parse = getattr(provider, "parse_strict")
    except AttributeError:
        strict_parse = None

    try:
        if callable(strict_parse):
            result = strict_parse(
                raw_text=raw_text,
                reference_year=reference_year,
                trigger=trigger,
                tenant_id=tenant_id,
            )
        else:
            result = provider.parse(
                raw_text=raw_text,
                reference_year=reference_year,
                trigger=trigger,
                tenant_id=tenant_id,
            )
    except LLMProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 -- wrapper turns provider crashes into classified failures
        raise LLMHTTPError("LLM provider call failed") from exc

    if result is None:
        raise LLMParseError("LLM provider returned no parse result")
    return result
