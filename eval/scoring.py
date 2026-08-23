"""
Per-case scoring across the task's dimensions:
  A. state extraction        -- only fields gold actually asserts (key present)
  B. multi-turn retention     -- the subset of A's fields that could only be right
                                  because they were carried over from history
  C. action/routing            -- normalized actual action vs. gold.expected_action
  D. response requirements     -- must_include / must_not_claim, deterministic only
  E. known-regression grouping -- by gold.source_label (done in report.py, using
                                  the CaseScore.source_label this module attaches)

Deterministic `==` throughout for A/B (dates as ISO strings, ints, bools) -- never an
LLM judge, per task section 3A.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.domain.inquiry_parser import parse_inquiry

from eval import action_taxonomy, response_requirements
from eval.replay import CaseResult

STATE_FIELDS: tuple[str, ...] = (
    "checkin_date",
    "checkout_date",
    "nights",
    "adult_count",
    "child_count",
    "infant_count",
    "guest_count",
    "has_pet",
    "pet_count",
    "room_count",
    "wants_bbq",
    "inquiry_type",
)

# No persisted equivalent in conversation_states -- approximated from the final
# turn's own (non-accumulated) parse_inquiry() call. Known evaluator limitation AND
# evidence of a real product gap (eval plan decision 6): a CONTEXT_MISS case where
# pet info was given in an earlier turn and not repeated in the final turn will
# legitimately fail these two fields, and that failure is a true finding, not a bug
# in the evaluator.
FINAL_TURN_ONLY_FIELDS: tuple[str, ...] = ("pet_type", "needs_pet_count_confirmation")

ALL_SCORED_FIELDS: tuple[str, ...] = STATE_FIELDS + FINAL_TURN_ONLY_FIELDS


@dataclass
class FieldScore:
    field: str
    expected: object
    actual: object
    passed: bool


@dataclass
class CaseScore:
    case_id: str
    source_label: str
    case_type: str
    field_scores: list[FieldScore] = field(default_factory=list)
    retention_field_scores: list[FieldScore] = field(default_factory=list)
    action_expected: str = ""
    action_actual: str = ""
    action_passed: bool = False
    must_include_results: dict[str, object] = field(default_factory=dict)
    must_not_claim_results: dict[str, object] = field(default_factory=dict)
    overall_passed: bool = False
    failure_reasons: list[str] = field(default_factory=list)


def _nights(checkin: str | None, checkout: str | None) -> int | None:
    if not checkin or not checkout:
        return None
    return (date.fromisoformat(checkout) - date.fromisoformat(checkin)).days


def _guest_count(adult: int | None, child: int | None) -> int | None:
    if adult is None and child is None:
        return None
    return (adult or 0) + (child or 0)


def derive_actual_fields(result: CaseResult, case: dict) -> dict:
    """Fields as the REPLAYED PIPELINE actually produced them: persisted
    conversation_states slots for anything that survives turns, plus a same-turn-only
    parse_inquiry() call for the two fields the app never persists."""
    state = result.final_state or {}
    checkin = state.get("checkin_date")
    checkout = state.get("checkout_date")
    adult = state.get("adult_count")
    child = state.get("child_count")
    has_pet = _ever_mentioned(case, result, lambda p: (p.pets.mentioned, p.pets.has_pet))
    wants_bbq = _ever_mentioned(case, result, lambda p: (p.bbq.mentioned, p.bbq.wants_bbq))

    final_parse = _final_turn_parse(case, result)

    return {
        "checkin_date": checkin,
        "checkout_date": checkout,
        "nights": _nights(checkin, checkout),
        "adult_count": adult,
        "child_count": child,
        "infant_count": state.get("infant_count"),
        "guest_count": _guest_count(adult, child),
        "has_pet": has_pet,
        "pet_count": state.get("pet_count"),
        "room_count": state.get("room_count"),
        "wants_bbq": wants_bbq,
        # KNOWN EVALUATOR LIMITATION: this is the FINAL message's own per-message
        # inquiry_intent (app.domain.inquiry_intent.parse_inquiry_intent), read
        # straight off the decision log. A "is_inquiry=False -> non_inquiry" mapping
        # was tried and reverted -- gold's non_inquiry/unknown split does not track
        # the app's is_inquiry flag consistently in multi-turn cases (STAGE C can
        # quote from ACCUMULATED state even when the final message's own per-message
        # classification is non-inquiry), so no deterministic normalization was found.
        # Comparing the raw value is the honest baseline; see eval plan / final report.
        "inquiry_type": result.final_decision.log_payload.get("inquiry_intent"),
        "pet_type": final_parse.pets.pet_type,
        "needs_pet_count_confirmation": final_parse.pets.needs_pet_count_confirmation,
    }


def _final_turn_parse(case: dict, result: CaseResult):
    reference_year = result.final_message.timestamp.year
    return parse_inquiry(case["input"], reference_year=reference_year)


def _history_texts(case: dict) -> list[str]:
    history = case.get("history") or []
    return [h["content"] for h in history] + [case["input"]]


def _ever_mentioned(case: dict, result: CaseResult, extract) -> bool | None:
    """Tri-state reconstruction for has_pet/wants_bbq: conversation_states.has_pet
    and .wants_bbq are `INTEGER NOT NULL DEFAULT 0` (schema.sql) -- the persisted
    state can only ever be True/False, never "customer never brought this up",
    even though the per-turn parser (and the log_payload tri-state it feeds) can
    tell the difference. Re-parses every turn's OWN text (no accumulated state,
    matching the eval's no-LLM/no-persistence-shortcuts design) and keeps the
    LAST turn that explicitly mentioned the field -- mirroring the COALESCE
    overwrite ConversationStateService actually applies turn over turn. Returns
    None only when NO turn ever mentioned it: a true "never discussed"."""
    reference_year = result.final_message.timestamp.year
    last_value: bool | None = None
    for text in _history_texts(case):
        parse = parse_inquiry(text, reference_year=reference_year)
        mentioned, value = extract(parse)
        if mentioned:
            last_value = value
    return last_value


def _single_turn_only_fields(case: dict, result: CaseResult) -> dict:
    """The final turn's text parsed WITHOUT any history -- used only to find which
    gold fields could exclusively be right thanks to carried-over state (dimension B).
    Not itself a scoring target."""
    parse = _final_turn_parse(case, result)
    adult = parse.guests.adult_count
    child = parse.guests.child_count
    has_pet = parse.pets.has_pet if parse.pets.mentioned else None
    wants_bbq = parse.bbq.wants_bbq if parse.bbq.mentioned else None
    return {
        "checkin_date": parse.dates.checkin_date,
        "checkout_date": parse.dates.checkout_date,
        "nights": _nights(parse.dates.checkin_date, parse.dates.checkout_date),
        "adult_count": adult,
        "child_count": child,
        "infant_count": parse.guests.infant_count,
        "guest_count": _guest_count(adult, child),
        "has_pet": has_pet,
        "pet_count": parse.pets.pet_count,
        "room_count": parse.room_count,
        "wants_bbq": wants_bbq,
        "inquiry_type": parse.intent.inquiry_type,
        "pet_type": parse.pets.pet_type,
        "needs_pet_count_confirmation": parse.pets.needs_pet_count_confirmation,
    }


def _asserted_fields(expected_fields: dict) -> list[str]:
    return [f for f in ALL_SCORED_FIELDS if f in expected_fields]


def score_fields(expected_fields: dict, actual_fields: dict) -> list[FieldScore]:
    scores = []
    for f in _asserted_fields(expected_fields):
        expected = expected_fields[f]
        actual = actual_fields.get(f)
        scores.append(FieldScore(field=f, expected=expected, actual=actual, passed=actual == expected))
    return scores


def score_retention_fields(
    case: dict, result: CaseResult, actual_fields: dict
) -> list[FieldScore]:
    if not case.get("history"):
        return []
    expected_fields = case["gold"]["expected_fields"]
    single_turn = _single_turn_only_fields(case, result)
    scores = []
    for f in _asserted_fields(expected_fields):
        expected = expected_fields[f]
        if expected is None:
            continue
        if single_turn.get(f) == expected:
            continue  # single-turn parse already gets this right; not a retention case
        actual = actual_fields.get(f)
        scores.append(FieldScore(field=f, expected=expected, actual=actual, passed=actual == expected))
    return scores


def score_case(case: dict, result: CaseResult) -> CaseScore:
    gold = case["gold"]
    expected_fields = gold["expected_fields"]
    actual_fields = derive_actual_fields(result, case)

    field_scores = score_fields(expected_fields, actual_fields)
    retention_field_scores = score_retention_fields(case, result, actual_fields)

    actual_action = action_taxonomy.classify_actual_action(
        result.final_decision, result.final_composed, result.final_state
    )
    expected_action = gold["expected_action"]
    action_passed = action_taxonomy.actions_match(actual_action, expected_action)

    requirements = gold.get("response_requirements", {})
    must_include_results = {
        tag: response_requirements.check_must_include(tag, result.final_composed)
        for tag in requirements.get("must_include", [])
    }
    must_not_claim_results = {
        tag: response_requirements.check_must_not_claim(tag, result.final_composed)
        for tag in requirements.get("must_not_claim", [])
    }

    failure_reasons = []
    for fs in field_scores:
        if not fs.passed:
            failure_reasons.append(f"field:{fs.field} expected={fs.expected!r} actual={fs.actual!r}")
    if not action_passed:
        failure_reasons.append(f"action expected={expected_action!r} actual={actual_action!r}")
    for tag, result_value in must_include_results.items():
        if result_value is False:
            failure_reasons.append(f"must_include:{tag} missing from reply")
    for tag, result_value in must_not_claim_results.items():
        if result_value is False:
            failure_reasons.append(f"must_not_claim:{tag} VIOLATED")

    overall_passed = (
        all(fs.passed for fs in field_scores)
        and action_passed
        and all(v is not False for v in must_include_results.values())
        and all(v is not False for v in must_not_claim_results.values())
    )

    return CaseScore(
        case_id=case["case_id"],
        source_label=case.get("source_label", ""),
        case_type=case.get("case_type", ""),
        field_scores=field_scores,
        retention_field_scores=retention_field_scores,
        action_expected=expected_action,
        action_actual=actual_action,
        action_passed=action_passed,
        must_include_results=must_include_results,
        must_not_claim_results=must_not_claim_results,
        overall_passed=overall_passed,
        failure_reasons=failure_reasons,
    )
