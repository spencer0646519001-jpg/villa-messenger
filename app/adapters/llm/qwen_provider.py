from app.adapters.llm.openrouter_base import OpenRouterProviderBase

DEFAULT_QWEN_MODEL = "qwen/qwen3.6-flash"


class QwenProvider(OpenRouterProviderBase):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_QWEN_MODEL,
        timeout_s: float = 5,
    ) -> None:
        super().__init__(api_key=api_key, model=model, timeout_s=timeout_s)
