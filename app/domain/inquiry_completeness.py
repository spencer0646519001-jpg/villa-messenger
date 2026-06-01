"""
Shared completeness rule for a quote — the single source of truth for "which
required slots are still missing".

Factored out of parse_inquiry so the per-message path (InquiryService) and the
accumulated multi-turn path (STAGE C reply composition) compute "complete" with
the SAME rule and can never diverge.

Pure: no I/O, no app imports. Callers supply already-derived values; in
particular guest_count is the caller's responsibility — the single-message path
passes the parser's guest_count, while the multi-turn path derives it from the
state's adult_count/child_count. The intent gate (only quote-relevant intents
ask for these slots) stays with the caller; this function answers only "given
that we want a quote, what's missing".
"""


def compute_missing_fields(
    *,
    checkin_date: str | None,
    checkout_date: str | None,
    guest_count: int | None,
    has_pet: bool,
    pet_count: int | None,
) -> list[str]:
    """Return the list of required-but-absent slot names, in stable order."""
    missing: list[str] = []
    if checkin_date is None:
        missing.append("checkin_date")
    if checkout_date is None:
        missing.append("checkout_date")
    if guest_count is None:
        missing.append("guest_count")
    if has_pet and pet_count is None:
        missing.append("pet_count")
    return missing
