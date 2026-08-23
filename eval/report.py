"""
Aggregate metrics + machine/human-readable output writers (task section 5/7).

Writes three artifacts per run:
  - results.jsonl  -- one row per case: case_id, per-dimension expected/actual/pass,
                      concise failure reason (machine-readable)
  - summary.json    -- all aggregate metrics below (machine-readable)
  - report.md        -- the same aggregates plus failing cases grouped by root cause
                      (human-readable)
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from eval.response_requirements import NOT_DETERMINISTIC
from eval.scoring import CaseScore

_DATE_FIELDS = ("checkin_date", "checkout_date", "nights")
_GUEST_FIELDS = ("adult_count", "child_count", "infant_count", "guest_count")
_ROOM_FIELDS = ("room_count",)
_PET_FIELDS = ("has_pet", "pet_count", "pet_type", "needs_pet_count_confirmation")
_BBQ_FIELDS = ("wants_bbq",)


def _rate(passed: int, total: int) -> float | None:
    return None if total == 0 else round(passed / total, 4)


def _bucket_accuracy(scores: list[CaseScore], fields: tuple[str, ...]) -> dict:
    relevant = [fs for cs in scores for fs in cs.field_scores if fs.field in fields]
    passed = sum(1 for fs in relevant if fs.passed)
    return {"passed": passed, "total": len(relevant), "accuracy": _rate(passed, len(relevant))}


def _requirement_pass_rate(scores: list[CaseScore], attr: str) -> dict:
    passed = total = not_deterministic = 0
    for cs in scores:
        for _tag, result in getattr(cs, attr).items():
            if result == NOT_DETERMINISTIC:
                not_deterministic += 1
                continue
            total += 1
            if result:
                passed += 1
    return {
        "passed": passed,
        "total": total,
        "not_deterministic": not_deterministic,
        "pass_rate": _rate(passed, total),
    }


def _group_rate(scores: list[CaseScore], predicate) -> dict:
    matching = [cs for cs in scores if predicate(cs)]
    passed = sum(1 for cs in matching if cs.overall_passed)
    return {"passed": passed, "total": len(matching)}


def compute_summary(scores: list[CaseScore]) -> dict:
    total = len(scores)
    case_passed = sum(1 for cs in scores if cs.overall_passed)

    all_field_scores = [fs for cs in scores for fs in cs.field_scores]
    all_field_passed = sum(1 for fs in all_field_scores if fs.passed)

    retention_scores = [fs for cs in scores for fs in cs.retention_field_scores]
    retention_passed = sum(1 for fs in retention_scores if fs.passed)

    action_passed = sum(1 for cs in scores if cs.action_passed)

    must_include = _requirement_pass_rate(scores, "must_include_results")
    must_not_claim = _requirement_pass_rate(scores, "must_not_claim_results")

    source_labels = sorted({cs.source_label for cs in scores if cs.source_label})
    by_source_label = {
        label: _group_rate(scores, lambda cs, label=label: cs.source_label == label)
        for label in source_labels
    }

    return {
        "total_cases": total,
        "case_level_pass_rate": {"passed": case_passed, "total": total, "rate": _rate(case_passed, total)},
        "state_extraction": {
            "overall": {
                "passed": all_field_passed,
                "total": len(all_field_scores),
                "accuracy": _rate(all_field_passed, len(all_field_scores)),
            },
            "date_accuracy": _bucket_accuracy(scores, _DATE_FIELDS),
            "guest_count_accuracy": _bucket_accuracy(scores, _GUEST_FIELDS),
            "room_count_accuracy": _bucket_accuracy(scores, _ROOM_FIELDS),
            "pet_accuracy": _bucket_accuracy(scores, _PET_FIELDS),
            "bbq_accuracy": _bucket_accuracy(scores, _BBQ_FIELDS),
        },
        "multi_turn_state_retention": {
            "passed": retention_passed,
            "total": len(retention_scores),
            "accuracy": _rate(retention_passed, len(retention_scores)),
        },
        "action_routing_accuracy": {
            "passed": action_passed,
            "total": total,
            "accuracy": _rate(action_passed, total),
        },
        "response_policy": {
            "must_include": must_include,
            "must_not_claim": must_not_claim,
        },
        "known_production_regressions": {
            "all_confirmed_failures": _group_rate(scores, lambda cs: cs.case_type == "failure"),
            "parser_miss": _group_rate(scores, lambda cs: cs.source_label == "PARSER_MISS"),
            "context_miss": _group_rate(scores, lambda cs: cs.source_label == "CONTEXT_MISS"),
        },
        "by_source_label": by_source_label,
    }


def write_results_jsonl(scores: list[CaseScore], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for cs in scores:
            f.write(json.dumps(_case_row(cs), ensure_ascii=False, default=str) + "\n")


def _case_row(cs: CaseScore) -> dict:
    row = asdict(cs)
    return row


def write_summary_json(summary: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report_md(summary: dict, scores: list[CaseScore], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Villa Messenger Eval v1 — BASELINE V1", ""]
    lines.append(f"Total cases: {summary['total_cases']}")
    clp = summary["case_level_pass_rate"]
    lines.append(f"Case-level pass rate: {clp['passed']}/{clp['total']} ({_pct(clp['rate'])})")
    lines.append("")

    lines.append("## State extraction accuracy")
    se = summary["state_extraction"]
    for name, key in [
        ("Overall", "overall"),
        ("Date fields", "date_accuracy"),
        ("Guest-count fields", "guest_count_accuracy"),
        ("Room-count field", "room_count_accuracy"),
        ("Pet fields", "pet_accuracy"),
        ("BBQ field", "bbq_accuracy"),
    ]:
        b = se[key]
        lines.append(f"- {name}: {b['passed']}/{b['total']} ({_pct(b['accuracy'])})")
    lines.append("")

    mt = summary["multi_turn_state_retention"]
    lines.append("## Multi-turn state retention accuracy")
    lines.append(f"- {mt['passed']}/{mt['total']} ({_pct(mt['accuracy'])})")
    lines.append("")

    ar = summary["action_routing_accuracy"]
    lines.append("## Action/routing accuracy")
    lines.append(f"- {ar['passed']}/{ar['total']} ({_pct(ar['accuracy'])})")
    lines.append("")

    lines.append("## Response-policy constraint pass rate (deterministic checks only)")
    for name, key in [("must_include", "must_include"), ("must_not_claim", "must_not_claim")]:
        r = summary["response_policy"][key]
        lines.append(
            f"- {name}: {r['passed']}/{r['total']} ({_pct(r['pass_rate'])}), "
            f"{r['not_deterministic']} tag-checks NOT_DETERMINISTIC (excluded)"
        )
    lines.append("")

    kpr = summary["known_production_regressions"]
    lines.append("## Known production failure regression")
    for name, key in [
        ("All confirmed production failures", "all_confirmed_failures"),
        ("Parser-miss regressions", "parser_miss"),
        ("Context-miss regressions", "context_miss"),
    ]:
        g = kpr[key]
        lines.append(f"- {name}: {g['passed']}/{g['total']}")
    lines.append("")

    lines.append("## By source_label")
    for label, g in sorted(summary["by_source_label"].items()):
        lines.append(f"- {label}: {g['passed']}/{g['total']}")
    lines.append("")

    lines.append("## Failing cases (grouped by source_label)")
    failing = [cs for cs in scores if not cs.overall_passed]
    by_label: dict[str, list[CaseScore]] = {}
    for cs in failing:
        by_label.setdefault(cs.source_label or "(none)", []).append(cs)
    for label, group in sorted(by_label.items()):
        lines.append(f"### {label} ({len(group)} failing)")
        for cs in group:
            reasons = "; ".join(cs.failure_reasons) or "(no reasons recorded)"
            lines.append(f"- **{cs.case_id}**: {reasons}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _pct(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate * 100:.1f}%"
