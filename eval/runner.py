"""
CLI entry point for Villa Messenger Eval v1.1.

    python -m eval.runner \\
        --gold ../villa_eval_private/eval_v1/expanded_gold_50_v1_1.jsonl \\
        --out eval/results/baseline_v1_1

Verifies the gold file's SHA-256 against the frozen hash before touching anything
else, and ABORTS (nonzero exit, no results written) on any mismatch -- the dataset is
frozen and this run must refuse to silently score a different file (task section 1).

To score the original v1 gold set instead, pass --gold pointing at
expanded_gold_50.jsonl and --expected-sha256 31ad2d77539a2250a1b9d04021373feca3496baed7e368d7ed04a32f93765688.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from pathlib import Path

from eval import report, replay, scoring

FROZEN_GOLD_SHA256 = "df6e9f4570a9edacba9796787601fe84acbf50580d8b11445db0152615291d94"

_DEFAULT_GOLD_PATH = (
    Path(__file__).resolve().parents[1]
    / ".."
    / "villa_eval_private"
    / "eval_v1"
    / "expanded_gold_50_v1_1.jsonl"
)


class GoldShaMismatchError(RuntimeError):
    pass


def verify_gold_sha(gold_path: str | Path, expected_sha256: str = FROZEN_GOLD_SHA256) -> str:
    data = Path(gold_path).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual.lower() != expected_sha256.lower():
        raise GoldShaMismatchError(
            f"Gold file SHA-256 mismatch for {gold_path}:\n"
            f"  expected: {expected_sha256}\n"
            f"  actual:   {actual}\n"
            "Aborting -- the frozen gold set must not be scored if it has changed."
        )
    return actual


def load_cases(gold_path: str | Path) -> list[dict]:
    cases = []
    with Path(gold_path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _error_case_score(case: dict, exc: Exception) -> scoring.CaseScore:
    return scoring.CaseScore(
        case_id=case.get("case_id", "(unknown)"),
        source_label=case.get("source_label", ""),
        case_type=case.get("case_type", ""),
        action_expected=case.get("gold", {}).get("expected_action", ""),
        action_actual="(replay_error)",
        action_passed=False,
        overall_passed=False,
        failure_reasons=[f"replay_error: {type(exc).__name__}: {exc}"],
    )


def run(cases: list[dict]) -> list[scoring.CaseScore]:
    scores = []
    for case in cases:
        try:
            result = replay.run_case(case)
            scores.append(scoring.score_case(case, result))
        except Exception as exc:  # noqa: BLE001 -- one bad case must not abort the run
            print(f"[eval] case {case.get('case_id')} raised during replay: {exc}", file=sys.stderr)
            traceback.print_exc()
            scores.append(_error_case_score(case, exc))
    return scores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Villa Messenger Eval v1.1 runner")
    parser.add_argument("--gold", default=str(_DEFAULT_GOLD_PATH), help="Path to expanded_gold_50_v1_1.jsonl")
    parser.add_argument("--out", default="eval/results/baseline_v1_1", help="Output directory")
    parser.add_argument(
        "--expected-sha256",
        default=FROZEN_GOLD_SHA256,
        help="Frozen gold SHA-256 to verify against (default: Eval v1.1 frozen hash)",
    )
    args = parser.parse_args(argv)

    try:
        sha = verify_gold_sha(args.gold, args.expected_sha256)
    except GoldShaMismatchError as exc:
        print(f"[eval] ABORT: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"[eval] ABORT: gold file not found: {exc}", file=sys.stderr)
        return 1
    print(f"[eval] gold SHA-256 verified: {sha}")

    cases = load_cases(args.gold)
    print(f"[eval] loaded {len(cases)} cases")

    scores = run(cases)

    out_dir = Path(args.out)
    report.write_results_jsonl(scores, out_dir / "results.jsonl")
    summary = report.compute_summary(scores)
    report.write_summary_json(summary, out_dir / "summary.json")
    report.write_report_md(summary, scores, out_dir / "report.md")

    print(f"[eval] wrote results to {out_dir}/")
    clp = summary["case_level_pass_rate"]
    print(f"[eval] case-level pass rate: {clp['passed']}/{clp['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
