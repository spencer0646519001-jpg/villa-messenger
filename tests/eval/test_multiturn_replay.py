from eval.replay import run_case

_HISTORY_TEXT = "8/10入住 8/11退房 4大2小 有空房嗎"


def _case(*, history: list[dict], case_id: str, user_id: str) -> dict:
    return {
        "case_id": case_id,
        "pseudonymous_user_id": user_id,
        "session_gap_hours": 1,
        "history": history,
        "input": "開2房",
        "production_reference": {"system_state_at_time": "on"},
        "gold": {"expected_action": "quoted", "expected_fields": {}},
    }


def test_history_is_actually_replayed_and_accumulates_state():
    with_history = _case(
        history=[{"role": "user", "content": _HISTORY_TEXT, "source_message_id": 1}],
        case_id="t-with-history",
        user_id="guest_test_with_history",
    )
    without_history = _case(history=[], case_id="t-without-history", user_id="guest_test_without_history")

    result_with = run_case(with_history)
    result_without = run_case(without_history)

    # Without history, "開2房" alone never opens a quote-relevant state (no dates/
    # guest count of its own) -- no accumulation happened, so there is no state at all.
    assert result_without.final_state is None

    # With history, the earlier turn's dates/guest-count slots must have carried over
    # into the state this turn reads and updates -- this turn only contributed
    # room_count=2, so if checkin/checkout/guest counts are present, they can only
    # have come from replaying the history turn first.
    state = result_with.final_state
    assert state is not None
    assert state["checkin_date"] == "2026-08-10"
    assert state["checkout_date"] == "2026-08-11"
    assert state["adult_count"] == 4
    assert state["child_count"] == 2
    assert state["room_count"] == 2


def test_case_with_history_actually_quotes_end_to_end():
    with_history = _case(
        history=[{"role": "user", "content": _HISTORY_TEXT, "source_message_id": 1}],
        case_id="t-quote",
        user_id="guest_test_quote",
    )

    result = run_case(with_history)

    assert result.final_composed.text is not None
    assert "報價" in result.final_composed.text or "NT$" in result.final_composed.text
