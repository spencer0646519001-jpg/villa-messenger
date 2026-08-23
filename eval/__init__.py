"""Villa Messenger Eval v1 — deterministic replay harness against the frozen gold set.

See docs/eval_v1_plan.md (or the task's own spec) for the full design. This package
never imports app.* in a way that mutates production data; it only builds throwaway
temp-SQLite pipelines per case, mirroring tests/test_line_webhook.py's fixtures.
"""
