from __future__ import annotations

import os
import time

from dotenv import load_dotenv

from app.adapters.llm.openrouter_base import OpenRouterProviderBase

EVAL_CASES = [
    {
        "id": "001a",
        "text": "13人 7/28-29",
        "reference_year": 2026,
        "trigger": "type_1_date_translation",
        "expect": {"checkin_date": "2026-07-28", "checkout_date": "2026-07-29"},
    },
    {
        "id": "001b",
        "text": "28入住29退房",
        "reference_year": 2026,
        "trigger": "type_1_date_translation",
        "expect": {"checkin_date": "2026-05-28", "checkout_date": "2026-05-29"},
        "note": "無月份脈絡時可接受 None+needs_clarification,需人工看輸出。",
    },
    {
        "id": "003",
        "text": "3/15入住3/17退房",
        "reference_year": 2026,
        "trigger": "type_2_intent_judgment",
        "expect": {
            "is_booking_intent": True,
            "checkin_date": "2026-03-15",
            "checkout_date": "2026-03-17",
        },
    },
    {
        "id": "neg1",
        "text": "合約3/15到期",
        "reference_year": 2026,
        "trigger": "type_2_intent_judgment",
        "expect": {"is_booking_intent": False},
    },
    {
        "id": "broad1",
        "text": "暑假大概想訂",
        "reference_year": 2026,
        "trigger": "type_1_date_translation",
        "expect": {
            "needs_clarification": True,
            "clarification_reason": "date_range_too_broad",
        },
    },
]

MODELS = ["deepseek/deepseek-v4-flash", "qwen/qwen3.6-flash"]


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is required")

    rows = [_eval_model(api_key, model) for model in MODELS]
    _print_table(rows)


def _eval_model(api_key: str, model: str) -> dict:
    provider = OpenRouterProviderBase(api_key=api_key, model=model, timeout_s=5)
    marks: list[str] = []
    latencies: list[float] = []
    for case in EVAL_CASES:
        started = time.perf_counter()
        out = provider.parse(
            raw_text=case["text"],
            reference_year=case["reference_year"],
            trigger=case["trigger"],
            tenant_id=0,
        )
        latencies.append(time.perf_counter() - started)
        marks.append("✓" if out is not None and _matches(out, case["expect"]) else "✗")
    return {"model": model, "marks": marks, "avg_latency": sum(latencies) / len(latencies)}


def _matches(output: object, expect: dict) -> bool:
    return all(getattr(output, key) == expected for key, expected in expect.items())


def _print_table(rows: list[dict]) -> None:
    case_ids = [case["id"] for case in EVAL_CASES]
    header = ["model", *case_ids, "pass%", "avg_latency"]
    print("  ".join(f"{column:<14}" for column in header))
    for row in rows:
        marks = row["marks"]
        pass_percent = f"{marks.count('✓') / len(marks):.0%}"
        cells = [row["model"], *marks, pass_percent, f"{row['avg_latency']:.1f}s"]
        print("  ".join(f"{cell:<14}" for cell in cells))


if __name__ == "__main__":
    main()
