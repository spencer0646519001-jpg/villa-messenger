from eval.scoring import score_fields


def test_score_fields_only_scores_asserted_keys():
    expected_fields = {"checkin_date": "2026-08-10", "guest_count": 4}
    actual_fields = {
        "checkin_date": "2026-08-10",
        "guest_count": 5,  # would fail if scored -- but it's not asserted
        "room_count": 2,  # not asserted either
    }

    scores = score_fields(expected_fields, actual_fields)

    scored_names = {fs.field for fs in scores}
    assert scored_names == {"checkin_date", "guest_count"}
    by_field = {fs.field: fs for fs in scores}
    assert by_field["checkin_date"].passed is True
    assert by_field["guest_count"].passed is False


def test_score_fields_distinguishes_null_assertion_from_unasserted():
    # child_count: null in gold IS an assertion ("should be unset"), not "don't care".
    expected_fields = {"child_count": None}
    scores_null_matches = score_fields(expected_fields, {"child_count": None})
    scores_null_mismatches = score_fields(expected_fields, {"child_count": 0})

    assert scores_null_matches[0].passed is True
    assert scores_null_mismatches[0].passed is False

    # A field key absent from expected_fields entirely is simply not scored.
    unscored = score_fields({}, {"child_count": 0})
    assert unscored == []
