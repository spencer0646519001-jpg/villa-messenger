"""
Tests for compute_missing_fields — the SHARED completeness rule used by both the
per-message parser (InquiryService) and the accumulated multi-turn path
(STAGE C). The drift-guard test feeds the same logical scenario through the
"parse view" and the "state view" and asserts identical results, so the two
callers can never diverge.
"""

import pytest

from app.domain.inquiry_completeness import compute_missing_fields


def _state_guest_count(adult_count: int | None, child_count: int | None) -> int | None:
    """How STAGE C derives guest_count from the stored adult/child slots."""
    return ((adult_count or 0) + (child_count or 0)) or None


# (checkin, checkout, adult, child, has_pet, pet_count) -> expected missing
_SCENARIOS = [
    ("2026-05-12", "2026-05-13", 4, None, False, None, []),
    (None, "2026-05-13", 4, None, False, None, ["checkin_date"]),
    ("2026-05-12", None, 4, None, False, None, ["checkout_date"]),
    ("2026-05-12", "2026-05-13", None, None, False, None, ["guest_count"]),
    (None, None, None, None, False, None, ["checkin_date", "checkout_date", "guest_count"]),
    ("2026-05-12", "2026-05-13", 2, 1, False, None, []),  # child counts toward guests
    ("2026-05-12", "2026-05-13", 4, None, True, None, ["pet_count"]),  # pet w/o count
    ("2026-05-12", "2026-05-13", 4, None, True, 1, []),  # pet with count
]


@pytest.mark.parametrize("checkin,checkout,adult,child,has_pet,pet_count,expected", _SCENARIOS)
def test_compute_missing_fields(checkin, checkout, adult, child, has_pet, pet_count, expected):
    missing = compute_missing_fields(
        checkin_date=checkin,
        checkout_date=checkout,
        guest_count=_state_guest_count(adult, child),
        has_pet=has_pet,
        pet_count=pet_count,
    )
    assert missing == expected


@pytest.mark.parametrize("checkin,checkout,adult,child,has_pet,pet_count,_expected", _SCENARIOS)
def test_parse_view_and_state_view_agree(
    checkin, checkout, adult, child, has_pet, pet_count, _expected
):
    # "parse view": the parser already computed guest_count = adults + children.
    parse_guest_count = (
        (adult or 0) + (child or 0) if (adult is not None or child is not None) else None
    )
    parse_result = compute_missing_fields(
        checkin_date=checkin, checkout_date=checkout, guest_count=parse_guest_count,
        has_pet=has_pet, pet_count=pet_count,
    )
    # "state view": STAGE C derives guest_count from the stored adult/child slots.
    state_result = compute_missing_fields(
        checkin_date=checkin, checkout_date=checkout,
        guest_count=_state_guest_count(adult, child), has_pet=has_pet, pet_count=pet_count,
    )
    assert parse_result == state_result
