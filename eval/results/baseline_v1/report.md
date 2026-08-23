# Villa Messenger Eval v1 — BASELINE V1

Total cases: 50
Case-level pass rate: 1/50 (2.0%)

## State extraction accuracy
- Overall: 452/606 (74.6%)
- Date fields: 127/150 (84.7%)
- Guest-count fields: 152/200 (76.0%)
- Room-count field: 49/50 (98.0%)
- Pet fields: 51/106 (48.1%)
- BBQ field: 36/50 (72.0%)

## Multi-turn state retention accuracy
- 82/188 (43.6%)

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
- LINKED_INQUIRY: 0/8
- MULTI_TURN: 0/10
- NON_INQUIRY: 0/6
- OFF_MODE_NATURAL_TRAFFIC: 0/2
- PARSER_MISS: 0/6
- TRUE_MISSING: 0/6
- TRUE_MISSING_ROOM_COUNT: 0/4
- full_house: 1/3
- urgent: 0/1

## Failing cases (grouped by source_label)
### CONTEXT_MISS (4 failing)
- **failure_161**: field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None; field:room_count expected=2 actual=4; field:inquiry_type expected='booking_question' actual='unknown'
- **failure_403**: field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None; field:pet_type expected='dog' actual=None; field:needs_pet_count_confirmation expected=True actual=False
- **failure_404**: field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None; field:inquiry_type expected='booking_question' actual='unknown'; field:pet_type expected='dog' actual=None; field:needs_pet_count_confirmation expected=True actual=False
- **failure_682**: field:adult_count expected=None actual=12; field:guest_count expected=None actual=12; field:pet_count expected=0 actual=None; field:inquiry_type expected='booking_question' actual='unknown'; action expected='missing_info' actual='missing_room_count'; must_include:ask_firm_guest_count missing from reply

### LINKED_INQUIRY (8 failing)
- **candidate_1**: field:pet_count expected=0 actual=None
- **candidate_2**: field:pet_count expected=0 actual=None
- **candidate_3**: field:pet_count expected=0 actual=None
- **candidate_5**: field:pet_count expected=0 actual=None
- **candidate_6**: field:pet_count expected=0 actual=None
- **candidate_7**: field:pet_count expected=0 actual=None
- **candidate_8**: field:pet_count expected=0 actual=None
- **candidate_12**: field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=False actual=None

### MULTI_TURN (10 failing)
- **candidate_4**: field:pet_count expected=0 actual=None
- **candidate_14**: field:pet_count expected=0 actual=None
- **candidate_25**: field:checkin_date expected='2026-08-08' actual=None; field:checkout_date expected='2026-08-09' actual=None; field:nights expected=1 actual=None; field:adult_count expected=6 actual=None; field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None; field:guest_count expected=6 actual=None; field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=True actual=None
- **candidate_26**: field:checkin_date expected='2026-08-08' actual=None; field:checkout_date expected='2026-08-09' actual=None; field:nights expected=1 actual=None; field:adult_count expected=6 actual=None; field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None; field:guest_count expected=6 actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=True actual=False
- **candidate_29**: field:infant_count expected=0 actual=None; field:pet_count expected=0 actual=None
- **candidate_30**: field:infant_count expected=0 actual=None; field:pet_count expected=0 actual=None
- **candidate_31**: field:infant_count expected=0 actual=None; field:has_pet expected=False actual=True; field:pet_count expected=0 actual=None
- **candidate_39**: field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=False actual=None
- **candidate_40**: field:checkin_date expected='2026-08-02' actual=None; field:checkout_date expected='2026-08-04' actual=None; field:nights expected=2 actual=None; field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=False actual=None
- **candidate_41**: field:checkin_date expected='2026-08-02' actual=None; field:checkout_date expected='2026-08-04' actual=None; field:nights expected=2 actual=None; field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=False actual=None

### NON_INQUIRY (6 failing)
- **candidate_10**: field:infant_count expected=0 actual=None; field:pet_count expected=0 actual=None; field:inquiry_type expected='non_inquiry' actual='unknown'; action expected='stale_context_reconfirm_then_missing_room_count' actual='missing_room_count'; must_include:stale_context_reconfirmation missing from reply
- **candidate_17**: field:checkout_date expected='2026-07-18' actual=None; field:nights expected=1 actual=None; field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=None actual=False
- **candidate_18**: field:checkout_date expected='2026-07-18' actual=None; field:nights expected=1 actual=None; field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=None actual=False; field:inquiry_type expected='non_inquiry' actual='unknown'; action expected='quoted' actual='missing_info'; must_include:quote_scope_disclaimer missing from reply
- **candidate_19**: field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=False actual=None; field:inquiry_type expected='non_inquiry' actual='unknown'
- **candidate_20**: field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=False actual=None; field:inquiry_type expected='non_inquiry' actual='unknown'
- **candidate_21**: field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=False actual=None

### OFF_MODE_NATURAL_TRAFFIC (2 failing)
- **candidate_13**: field:has_pet expected=False actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=False actual=None
- **candidate_15**: field:pet_count expected=0 actual=None

### PARSER_MISS (6 failing)
- **failure_326**: field:checkin_date expected='2026-09-25' actual=None; field:adult_count expected=10 actual=None; field:child_count expected=2 actual=None; field:infant_count expected=0 actual=None; field:guest_count expected=12 actual=None; field:pet_count expected=0 actual=None; field:wants_bbq expected=True actual=False
- **failure_345**: field:adult_count expected=None actual=2; field:guest_count expected=None actual=2; must_include:ask_firm_guest_count missing from reply
- **failure_402**: field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None
- **failure_557**: action expected='ask_date_range_clarification' actual='faq'; must_include:date_range_clarification missing from reply; must_include:ask_firm_guest_count missing from reply
- **failure_558**: field:adult_count expected=None actual=20; field:guest_count expected=None actual=20; field:has_pet expected=False actual=True; field:inquiry_type expected='availability' actual='booking_question'; action expected='ask_date_range_clarification' actual='missing_info'; must_include:date_range_clarification missing from reply; must_include:ask_firm_guest_count missing from reply
- **failure_681**: field:adult_count expected=None actual=12; field:guest_count expected=None actual=12; field:pet_count expected=0 actual=None; must_include:ask_firm_guest_count missing from reply

### TRUE_MISSING (6 failing)
- **control_9**: field:pet_count expected=0 actual=None
- **control_159**: field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None; field:wants_bbq expected=None actual=False
- **control_427**: field:pet_count expected=0 actual=None; field:inquiry_type expected='booking_question' actual='unknown'
- **control_500**: field:pet_count expected=0 actual=None; field:inquiry_type expected='non_inquiry' actual='booking_question'; action expected='non_inquiry_uncategorized' actual='missing_info'
- **control_516**: field:pet_count expected=0 actual=None
- **control_708**: field:pet_count expected=0 actual=None; field:inquiry_type expected='non_inquiry' actual='price'; action expected='non_inquiry_uncategorized' actual='missing_info'

### TRUE_MISSING_ROOM_COUNT (4 failing)
- **control_11**: field:checkout_date expected='2026-07-12' actual='2026-07-26'; field:nights expected=1 actual=15; field:child_count expected=0 actual=2; field:infant_count expected=0 actual=None; field:guest_count expected=10 actual=12; field:pet_count expected=0 actual=None
- **control_16**: field:checkout_date expected='2026-07-18' actual=None; field:nights expected=1 actual=None; field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None; field:pet_count expected=0 actual=None; action expected='missing_room_count' actual='missing_info'; must_include:ask_room_count missing from reply
- **control_32**: field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None; field:pet_count expected=0 actual=None; must_include:guest_count_aware_minimum_room_suggestion(at_least_4_rooms_for_14) missing from reply
- **control_193**: field:checkout_date expected='2026-08-23' actual=None; field:nights expected=2 actual=None; field:child_count expected=0 actual=None; field:infant_count expected=0 actual=None; field:pet_count expected=0 actual=None; action expected='missing_room_count' actual='missing_info'; must_include:ask_room_count missing from reply

### full_house (2 failing)
- **candidate_426**: field:pet_count expected=0 actual=None; field:inquiry_type expected='price' actual='availability'
- **candidate_517**: field:infant_count expected=0 actual=None

### urgent (1 failing)
- **candidate_711**: field:pet_count expected=0 actual=None; field:inquiry_type expected='unknown' actual=None
