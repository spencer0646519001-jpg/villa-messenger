from __future__ import annotations

import json
import logging
import re

from app.domain.llm_provider import LLMOutput

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ALLOWED_INTENTS = {"price", "availability", "booking", "faq", "other", "unknown"}
_CLARIFICATION_REASON = "date_range_too_broad"


def call_openrouter(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_text: str,
    timeout_s: float,
) -> str | None:
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("OpenAI SDK is not installed; LLM fallback disabled for this call")
        return None

    try:
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            response_format={"type": "json_object"},
            timeout=timeout_s,
        )
        content = response.choices[0].message.content
    except Exception:  # noqa: BLE001 -- provider failures must degrade to rules
        logger.warning("OpenRouter LLM call failed", exc_info=True)
        return None

    if not isinstance(content, str):
        logger.warning("OpenRouter LLM returned non-string content")
        return None
    return content


class OpenRouterProviderBase:
    def __init__(self, *, api_key: str, model: str, timeout_s: float = 5) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s

    def parse(
        self,
        *,
        raw_text: str,
        reference_year: int,
        trigger: str,
        tenant_id: int,
    ) -> LLMOutput | None:
        _ = tenant_id
        raw_json = call_openrouter(
            api_key=self._api_key,
            model=self._model,
            system_prompt=_build_system_prompt(reference_year, trigger),
            user_text=raw_text,
            timeout_s=self._timeout_s,
        )
        if raw_json is None:
            return None
        return _parse_llm_output(raw_json)


def _parse_llm_output(raw_json: str) -> LLMOutput | None:
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.warning("LLM provider returned invalid JSON")
        return None
    if not isinstance(data, dict):
        logger.warning("LLM provider returned JSON that is not an object")
        return None
    return LLMOutput(
        intent=_intent_or_none(data.get("intent")),
        checkin_date=_date_or_none(data.get("checkin_date")),
        checkout_date=_date_or_none(data.get("checkout_date")),
        adult_count=_int_or_none(data.get("adult_count")),
        child_count=_int_or_none(data.get("child_count")),
        infant_count=_int_or_none(data.get("infant_count")),
        pet_count=_int_or_none(data.get("pet_count")),
        has_pet=_bool_or_none(data.get("has_pet")),
        last_message_text=_str_or_none(data.get("last_message_text")),
        is_booking_intent=_bool_or_none(data.get("is_booking_intent")),
        needs_clarification=_bool_or_false(data.get("needs_clarification")),
        clarification_reason=_clarification_reason_or_none(data.get("clarification_reason")),
        room_count=_int_or_none(data.get("room_count")),
    )


def _intent_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value in _ALLOWED_INTENTS else None


def _date_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and _DATE_RE.fullmatch(value) else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _bool_or_false(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _clarification_reason_or_none(value: object) -> str | None:
    return value if value == _CLARIFICATION_REASON else None


def _build_system_prompt(reference_year: int, trigger: str) -> str:
    return f"""
你是民宿訂房訊息的欄位抽取器。只輸出 JSON,不要解釋,不要產生給客人的回覆文字。

任務:
- 從客人訊息抽出入住日期、退房日期、人數、寵物、意圖。
- 日期一律輸出 YYYY-MM-DD。沒有明寫年份時使用 {reference_year}。
- 無法判斷的欄位填 null。
- tenant_id 不在 prompt 中使用,也不可輸出。

trigger: {trigger}

簡寫日期範例:
- "7/28-29" => checkin_date "2026-07-28", checkout_date "2026-07-29"
- "28入住29退房" 若沒有月份脈絡,日期欄位填 null。

裸日期意圖判斷:
- 像訂房或詢價,例如入住退房日期、想詢價、想訂房 => is_booking_intent true。
- 只是提到日期但非訂房,例如合約到期、上次來過 => is_booking_intent false。

範圍太大且無法補出確切日期,例如暑假、下個月:
- needs_clarification true
- clarification_reason "date_range_too_broad"
- 日期欄位填 null

JSON schema:
{{
  "intent": "price|availability|booking|faq|other|unknown|null",
  "checkin_date": "YYYY-MM-DD|null",
  "checkout_date": "YYYY-MM-DD|null",
  "adult_count": "integer|null",
  "child_count": "integer|null",
  "infant_count": "integer|null",
  "pet_count": "integer|null",
  "room_count": "integer|null",
  "has_pet": "boolean|null",
  "last_message_text": "string|null",
  "is_booking_intent": "boolean|null",
  "needs_clarification": "boolean",
  "clarification_reason": "date_range_too_broad|null"
}}
""".strip()
