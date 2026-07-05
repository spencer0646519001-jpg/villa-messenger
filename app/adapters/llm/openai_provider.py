from app.adapters.llm.openrouter_base import OpenRouterProviderBase

DEFAULT_OPENAI_MODEL = "openai/gpt-4o-mini"


class OpenAIProvider(OpenRouterProviderBase):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_OPENAI_MODEL,
        timeout_s: float = 5,
    ) -> None:
        super().__init__(api_key=api_key, model=model, timeout_s=timeout_s)
