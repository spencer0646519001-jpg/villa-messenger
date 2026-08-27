# Villa Messenger Eval v1 — BASELINE V1

Total cases: 50
Case-level pass rate: 30/50 (60.0%)

## State extraction accuracy
- Overall: 465/502 (92.6%)
- Date fields: 136/150 (90.7%)
- Guest-count fields: 196/200 (98.0%)
- Room-count field: 49/50 (98.0%)
- Pet fields: 26/34 (76.5%)
- BBQ field: 14/18 (77.8%)

## Multi-turn state retention accuracy
- 63/83 (75.9%)

## Action/routing accuracy
- 45/50 (90.0%)

## Response-policy constraint pass rate (deterministic checks only)
- must_include: 23/30 (76.7%), 0 tag-checks NOT_DETERMINISTIC (excluded)
- must_not_claim: 55/55 (100.0%), 0 tag-checks NOT_DETERMINISTIC (excluded)

## Known production failure regression
- All confirmed production failures: 2/10
- Parser-miss regressions: 1/6
- Context-miss regressions: 1/4

## By source_label
- CONTEXT_MISS: 1/4
- LINKED_INQUIRY: 8/8
- MULTI_TURN: 5/10
- NON_INQUIRY: 4/6
- OFF_MODE_NATURAL_TRAFFIC: 2/2
- PARSER_MISS: 1/6
- TRUE_MISSING: 4/6
- TRUE_MISSING_ROOM_COUNT: 2/4
- full_house: 2/3
- urgent: 1/1

## Failing cases (grouped by source_label)
### CONTEXT_MISS (3 failing)
- **failure_161**: field:room_count expected=2 actual=4
- **failure_403**: field:pet_type expected='dog' actual=None; field:needs_pet_count_confirmation expected=True actual=False
- **failure_404**: field:pet_type expected='dog' actual=None; field:needs_pet_count_confirmation expected=True actual=False

### MULTI_TURN (5 failing)
- **candidate_29**: field:checkout_date expected=None actual='2026-08-25'; field:nights expected=None actual=2; field:wants_bbq expected=False actual=None
- **candidate_30**: field:checkout_date expected=None actual='2026-08-25'; field:nights expected=None actual=2; field:wants_bbq expected=False actual=None
- **candidate_31**: field:checkout_date expected=None actual='2026-08-25'; field:nights expected=None actual=2; field:has_pet expected=None actual=True; field:wants_bbq expected=False actual=None
- **candidate_40**: field:checkin_date expected='2026-08-02' actual=None; field:checkout_date expected='2026-08-04' actual=None; field:nights expected=2 actual=None
- **candidate_41**: field:checkin_date expected='2026-08-02' actual=None; field:checkout_date expected='2026-08-04' actual=None; field:nights expected=2 actual=None

### NON_INQUIRY (2 failing)
- **candidate_10**: field:inquiry_type expected='non_inquiry' actual='unknown'; action expected='stale_context_reconfirm_then_missing_room_count' actual='missing_room_count'; must_include:stale_context_reconfirmation missing from reply
- **candidate_18**: field:inquiry_type expected='non_inquiry' actual='unknown'

### PARSER_MISS (5 failing)
- **failure_326**: field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=True actual=None
- **failure_345**: field:adult_count expected=None actual=2; field:guest_count expected=None actual=2; must_include:ask_firm_guest_count missing from reply
- **failure_557**: action expected='ask_date_range_clarification' actual='faq'; must_include:date_range_clarification missing from reply; must_include:ask_firm_guest_count missing from reply
- **failure_558**: field:has_pet expected=False actual=True; field:inquiry_type expected='availability' actual='booking_question'; action expected='ask_date_range_clarification' actual='faq'; must_include:date_range_clarification missing from reply; must_include:ask_firm_guest_count missing from reply
- **failure_681**: field:checkout_date expected=None actual='2026-10-26'; field:nights expected=None actual=2

### TRUE_MISSING (2 failing)
- **control_500**: field:inquiry_type expected='non_inquiry' actual='booking_question'; action expected='non_inquiry_uncategorized' actual='missing_info'
- **control_708**: field:inquiry_type expected='non_inquiry' actual='price'; action expected='non_inquiry_uncategorized' actual='missing_info'

### TRUE_MISSING_ROOM_COUNT (2 failing)
- **control_11**: field:child_count expected=None actual=2; field:guest_count expected=10 actual=12
- **control_32**: must_include:guest_count_aware_minimum_room_suggestion(at_least_4_rooms_for_14) missing from reply

### full_house (1 failing)
- **candidate_426**: field:inquiry_type expected='price' actual='availability'
