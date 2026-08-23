from eval.report import compute_summary
from eval.response_requirements import NOT_DETERMINISTIC
from eval.scoring import CaseScore, FieldScore


def _make_case(
    *,
    case_id: str,
    source_label: str,
    case_type: str,
    field_results: list[tuple[str, bool]],
    action_passed: bool,
    must_include: dict | None = None,
    must_not_claim: dict | None = None,
) -> CaseScore:
    field_scores = [
        FieldScore(field=name, expected="x", actual="x" if passed else "y", passed=passed)
        for name, passed in field_results
    ]
    overall_passed = (
        all(passed for _n, passed in field_results)
        and action_passed
        and all(v is not False for v in (must_include or {}).values())
        and all(v is not False for v in (must_not_claim or {}).values())
    )
    return CaseScore(
        case_id=case_id,
        source_label=source_label,
        case_type=case_type,
        field_scores=field_scores,
        action_expected="quoted",
        action_actual="quoted" if action_passed else "missing_info",
        action_passed=action_passed,
        must_include_results=must_include or {},
        must_not_claim_results=must_not_claim or {},
        overall_passed=overall_passed,
    )


def test_case_level_pass_rate_and_field_accuracy():
    scores = [
        _make_case(
            case_id="c1",
            source_label="PARSER_MISS",
            case_type="failure",
            field_results=[("checkin_date", True), ("guest_count", True)],
            action_passed=True,
        ),
        _make_case(
            case_id="c2",
            source_label="PARSER_MISS",
            case_type="failure",
            field_results=[("checkin_date", True), ("guest_count", False)],
            action_passed=True,
        ),
        _make_case(
            case_id="c3",
            source_label="CONTEXT_MISS",
            case_type="failure",
            field_results=[("checkin_date", False)],
            action_passed=False,
        ),
    ]

    summary = compute_summary(scores)

    assert summary["total_cases"] == 3
    # c1 passes everything; c2 fails a field; c3 fails a field AND the action.
    assert summary["case_level_pass_rate"] == {"passed": 1, "total": 3, "rate": round(1 / 3, 4)}

    # 5 total field checks across all cases (2 + 2 + 1), 3 pass.
    assert summary["state_extraction"]["overall"] == {"passed": 3, "total": 5, "accuracy": 0.6}

    assert summary["action_routing_accuracy"] == {"passed": 2, "total": 3, "accuracy": round(2 / 3, 4)}

    assert summary["known_production_regressions"]["parser_miss"] == {"passed": 1, "total": 2}
    assert summary["known_production_regressions"]["context_miss"] == {"passed": 0, "total": 1}
    assert summary["known_production_regressions"]["all_confirmed_failures"] == {"passed": 1, "total": 3}

    assert summary["by_source_label"]["PARSER_MISS"] == {"passed": 1, "total": 2}
    assert summary["by_source_label"]["CONTEXT_MISS"] == {"passed": 0, "total": 1}


def test_response_policy_pass_rate_excludes_not_deterministic():
    scores = [
        _make_case(
            case_id="c1",
            source_label="X",
            case_type="candidate",
            field_results=[],
            action_passed=True,
            must_include={"ask_room_count": True, "some_unregistered_tag": NOT_DETERMINISTIC},
            must_not_claim={"guarantee_availability": True},
        ),
        _make_case(
            case_id="c2",
            source_label="X",
            case_type="candidate",
            field_results=[],
            action_passed=True,
            must_include={"ask_room_count": False},
            must_not_claim={},
        ),
    ]

    summary = compute_summary(scores)

    mi = summary["response_policy"]["must_include"]
    assert mi["total"] == 2  # the NOT_DETERMINISTIC tag is excluded from the denominator
    assert mi["passed"] == 1
    assert mi["not_deterministic"] == 1

    mnc = summary["response_policy"]["must_not_claim"]
    assert mnc == {"passed": 1, "total": 1, "not_deterministic": 0, "pass_rate": 1.0}


def test_empty_scores_do_not_divide_by_zero():
    summary = compute_summary([])
    assert summary["case_level_pass_rate"]["rate"] is None
    assert summary["state_extraction"]["overall"]["accuracy"] is None
