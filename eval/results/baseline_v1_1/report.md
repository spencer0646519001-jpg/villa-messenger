# Villa Messenger Eval v1 — BASELINE V1

Total cases: 50
Case-level pass rate: 20/50 (40.0%)

## State extraction accuracy
- Overall: 428/502 (85.3%)
- Date fields: 127/150 (84.7%)
- Guest-count fields: 183/200 (91.5%)
- Room-count field: 49/50 (98.0%)
- Pet fields: 24/34 (70.6%)
- BBQ field: 8/18 (44.4%)

## Multi-turn state retention accuracy
- 45/90 (50.0%)

## Action/routing accuracy
- 41/50 (82.0%)

## Response-policy constraint pass rate (deterministic checks only)
- must_include: 18/30 (60.0%), 0 tag-checks NOT_DETERMINISTIC (excluded)
- must_not_claim: 55/55 (100.0%), 0 tag-checks NOT_DETERMINISTIC (excluded)

## Known production failure regression
- All confirmed production failures: 0/10
- Parser-miss regressions: 0/6
- Context-miss regressions: 0/4

## By source_label
- CONTEXT_MISS: 0/4
- LINKED_INQUIRY: 8/8
- MULTI_TURN: 5/10
- NON_INQUIRY: 1/6
- OFF_MODE_NATURAL_TRAFFIC: 2/2
- PARSER_MISS: 0/6
- TRUE_MISSING: 2/6
- TRUE_MISSING_ROOM_COUNT: 0/4
- full_house: 2/3
- urgent: 0/1

## Failing cases (grouped by source_label)
### CONTEXT_MISS (4 failing)
- **failure_161**: field:room_count expected=2 actual=4; field:wants_bbq expected=None actual=False; field:inquiry_type expected='booking_question' actual='unknown'
- **failure_403**: field:wants_bbq expected=None actual=False; field:pet_type expected='dog' actual=None; field:needs_pet_count_confirmation expected=True actual=False
- **failure_404**: field:wants_bbq expected=None actual=False; field:inquiry_type expected='booking_question' actual='unknown'; field:pet_type expected='dog' actual=None; field:needs_pet_count_confirmation expected=True actual=False
- **failure_682**: field:adult_count expected=None actual=12; field:guest_count expected=None actual=12; field:inquiry_type expected='booking_question' actual='unknown'; action expected='missing_info' actual='missing_room_count'; must_include:ask_firm_guest_count missing from reply

### MULTI_TURN (5 failing)
- **candidate_25**: field:checkin_date expected='2026-08-08' actual=None; field:checkout_date expected='2026-08-09' actual=None; field:nights expected=1 actual=None; field:adult_count expected=6 actual=None; field:guest_count expected=6 actual=None; field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=True actual=None
- **candidate_26**: field:checkin_date expected='2026-08-08' actual=None; field:checkout_date expected='2026-08-09' actual=None; field:nights expected=1 actual=None; field:adult_count expected=6 actual=None; field:guest_count expected=6 actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=True actual=False
- **candidate_31**: field:has_pet expected=None actual=True
- **candidate_40**: field:checkin_date expected='2026-08-02' actual=None; field:checkout_date expected='2026-08-04' actual=None; field:nights expected=2 actual=None
- **candidate_41**: field:checkin_date expected='2026-08-02' actual=None; field:checkout_date expected='2026-08-04' actual=None; field:nights expected=2 actual=None

### NON_INQUIRY (5 failing)
- **candidate_10**: field:inquiry_type expected='non_inquiry' actual='unknown'; action expected='stale_context_reconfirm_then_missing_room_count' actual='missing_room_count'; must_include:stale_context_reconfirmation missing from reply
- **candidate_17**: field:checkout_date expected='2026-07-18' actual=None; field:nights expected=1 actual=None; field:wants_bbq expected=None actual=False
- **candidate_18**: field:checkout_date expected='2026-07-18' actual=None; field:nights expected=1 actual=None; field:wants_bbq expected=None actual=False; field:inquiry_type expected='non_inquiry' actual='unknown'; action expected='quoted' actual='missing_info'; must_include:quote_scope_disclaimer missing from reply
- **candidate_19**: field:inquiry_type expected='non_inquiry' actual='unknown'
- **candidate_20**: field:inquiry_type expected='non_inquiry' actual='unknown'

### PARSER_MISS (6 failing)
- **failure_326**: field:checkin_date expected='2026-09-25' actual=None; field:adult_count expected=10 actual=None; field:child_count expected=2 actual=None; field:guest_count expected=12 actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=True actual=False
- **failure_345**: field:adult_count expected=None actual=2; field:guest_count expected=None actual=2; must_include:ask_firm_guest_count missing from reply
- **failure_402**: field:wants_bbq expected=None actual=False
- **failure_557**: action expected='ask_date_range_clarification' actual='faq'; must_include:date_range_clarification missing from reply; must_include:ask_firm_guest_count missing from reply
- **failure_558**: field:adult_count expected=None actual=20; field:guest_count expected=None actual=20; field:has_pet expected=False actual=True; field:inquiry_type expected='availability' actual='booking_question'; action expected='ask_date_range_clarification' actual='missing_info'; must_include:date_range_clarification missing from reply; must_include:ask_firm_guest_count missing from reply
- **failure_681**: field:adult_count expected=None actual=12; field:guest_count expected=None actual=12; must_include:ask_firm_guest_count missing from reply

### TRUE_MISSING (4 failing)
- **control_159**: field:wants_bbq expected=None actual=False
- **control_427**: field:inquiry_type expected='booking_question' actual='unknown'
- **control_500**: field:inquiry_type expected='non_inquiry' actual='booking_question'; action expected='non_inquiry_uncategorized' actual='missing_info'
- **control_708**: field:inquiry_type expected='non_inquiry' actual='price'; action expected='non_inquiry_uncategorized' actual='missing_info'

### TRUE_MISSING_ROOM_COUNT (4 failing)
- **control_11**: field:checkout_date expected='2026-07-12' actual='2026-07-26'; field:nights expected=1 actual=15; field:child_count expected=None actual=2; field:guest_count expected=10 actual=12
- **control_16**: field:checkout_date expected='2026-07-18' actual=None; field:nights expected=1 actual=None; action expected='missing_room_count' actual='missing_info'; must_include:ask_room_count missing from reply
- **control_32**: must_include:guest_count_aware_minimum_room_suggestion(at_least_4_rooms_for_14) missing from reply
- **control_193**: field:checkout_date expected='2026-08-23' actual=None; field:nights expected=2 actual=None; action expected='missing_room_count' actual='missing_info'; must_include:ask_room_count missing from reply

### full_house (1 failing)
- **candidate_426**: field:inquiry_type expected='price' actual='availability'

### urgent (1 failing)
- **candidate_711**: field:inquiry_type expected='unknown' actual=None
