# Villa Messenger Eval v1 — BASELINE V1

Total cases: 50
Case-level pass rate: 26/50 (52.0%)

## State extraction accuracy
- Overall: 457/502 (91.0%)
- Date fields: 134/150 (89.3%)
- Guest-count fields: 190/200 (95.0%)
- Room-count field: 49/50 (98.0%)
- Pet fields: 26/34 (76.5%)
- BBQ field: 14/18 (77.8%)

## Multi-turn state retention accuracy
- 59/85 (69.4%)

## Action/routing accuracy
- 41/50 (82.0%)

## Response-policy constraint pass rate (deterministic checks only)
- must_include: 18/30 (60.0%), 0 tag-checks NOT_DETERMINISTIC (excluded)
- must_not_claim: 55/55 (100.0%), 0 tag-checks NOT_DETERMINISTIC (excluded)

## Known production failure regression
- All confirmed production failures: 1/10
- Parser-miss regressions: 1/6
- Context-miss regressions: 0/4

## By source_label
- CONTEXT_MISS: 0/4
- LINKED_INQUIRY: 8/8
- MULTI_TURN: 5/10
- NON_INQUIRY: 3/6
- OFF_MODE_NATURAL_TRAFFIC: 2/2
- PARSER_MISS: 1/6
- TRUE_MISSING: 4/6
- TRUE_MISSING_ROOM_COUNT: 0/4
- full_house: 2/3
- urgent: 1/1

## Failing cases (grouped by source_label)
### CONTEXT_MISS (4 failing)
- **failure_161**: field:room_count expected=2 actual=4
- **failure_403**: field:pet_type expected='dog' actual=None; field:needs_pet_count_confirmation expected=True actual=False
- **failure_404**: field:pet_type expected='dog' actual=None; field:needs_pet_count_confirmation expected=True actual=False
- **failure_682**: field:adult_count expected=None actual=12; field:guest_count expected=None actual=12; action expected='missing_info' actual='missing_room_count'; must_include:ask_firm_guest_count missing from reply

### MULTI_TURN (5 failing)
- **candidate_29**: field:wants_bbq expected=False actual=None
- **candidate_30**: field:wants_bbq expected=False actual=None
- **candidate_31**: field:has_pet expected=None actual=True; field:wants_bbq expected=False actual=None
- **candidate_40**: field:checkin_date expected='2026-08-02' actual=None; field:checkout_date expected='2026-08-04' actual=None; field:nights expected=2 actual=None
- **candidate_41**: field:checkin_date expected='2026-08-02' actual=None; field:checkout_date expected='2026-08-04' actual=None; field:nights expected=2 actual=None

### NON_INQUIRY (3 failing)
- **candidate_10**: field:inquiry_type expected='non_inquiry' actual='unknown'; action expected='stale_context_reconfirm_then_missing_room_count' actual='missing_room_count'; must_include:stale_context_reconfirmation missing from reply
- **candidate_17**: field:checkout_date expected='2026-07-18' actual=None; field:nights expected=1 actual=None
- **candidate_18**: field:checkout_date expected='2026-07-18' actual=None; field:nights expected=1 actual=None; field:inquiry_type expected='non_inquiry' actual='unknown'; action expected='quoted' actual='missing_info'; must_include:quote_scope_disclaimer missing from reply

### PARSER_MISS (5 failing)
- **failure_326**: field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=True actual=None
- **failure_345**: field:adult_count expected=None actual=2; field:guest_count expected=None actual=2; must_include:ask_firm_guest_count missing from reply
- **failure_557**: action expected='ask_date_range_clarification' actual='faq'; must_include:date_range_clarification missing from reply; must_include:ask_firm_guest_count missing from reply
- **failure_558**: field:adult_count expected=None actual=20; field:guest_count expected=None actual=20; field:has_pet expected=False actual=True; field:inquiry_type expected='availability' actual='booking_question'; action expected='ask_date_range_clarification' actual='missing_info'; must_include:date_range_clarification missing from reply; must_include:ask_firm_guest_count missing from reply
- **failure_681**: field:adult_count expected=None actual=12; field:guest_count expected=None actual=12; must_include:ask_firm_guest_count missing from reply

### TRUE_MISSING (2 failing)
- **control_500**: field:inquiry_type expected='non_inquiry' actual='booking_question'; action expected='non_inquiry_uncategorized' actual='missing_info'
- **control_708**: field:inquiry_type expected='non_inquiry' actual='price'; action expected='non_inquiry_uncategorized' actual='missing_info'

### TRUE_MISSING_ROOM_COUNT (4 failing)
- **control_11**: field:checkout_date expected='2026-07-12' actual='2026-07-26'; field:nights expected=1 actual=15; field:child_count expected=None actual=2; field:guest_count expected=10 actual=12
- **control_16**: field:checkout_date expected='2026-07-18' actual=None; field:nights expected=1 actual=None; action expected='missing_room_count' actual='missing_info'; must_include:ask_room_count missing from reply
- **control_32**: must_include:guest_count_aware_minimum_room_suggestion(at_least_4_rooms_for_14) missing from reply
- **control_193**: field:checkout_date expected='2026-08-23' actual=None; field:nights expected=2 actual=None; action expected='missing_room_count' actual='missing_info'; must_include:ask_room_count missing from reply

### full_house (1 failing)
- **candidate_426**: field:inquiry_type expected='price' actual='availability'
