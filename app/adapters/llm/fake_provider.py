from app.domain.llm_provider import LLMOutput


class FakeProvider:
    def __init__(
        self,
        output: LLMOutput | None = None,
        *,
        outputs_by_text: dict[str, LLMOutput | None] | None = None,
    ) -> None:
        self._output = output
        self._outputs_by_text = outputs_by_text or {}
        self.calls: list[dict[str, object]] = []

    def parse(
        self,
        *,
        raw_text: str,
        reference_year: int,
        trigger: str,
        tenant_id: int,
    ) -> LLMOutput | None:
        self.calls.append(
            {
                "raw_text": raw_text,
                "reference_year": reference_year,
                "trigger": trigger,
                "tenant_id": tenant_id,
            }
        )
        return self._outputs_by_text.get(raw_text, self._output)
