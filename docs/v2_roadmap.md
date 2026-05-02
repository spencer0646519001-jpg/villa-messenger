# V2 Roadmap

## Calendar Availability

V2 should add Google Calendar availability checks.

Requirements:

- Availability checks remain tenant-aware.
- Each tenant may define its own booking keyword.
- `zhen123-house` keyword: `枕123`.
- Future tenant example: `uncle-homestay` may use keyword `妃`.
- The system must support any number of tenants.

Calendar results must still be treated carefully. Even with calendar support, guest-facing replies should avoid over-promising unless staff approval rules explicitly allow it.

## Holiday Data

Taiwan holiday API support is V2. V1.5 should use configured `special_dates` for national holidays and spring festival dates.

## Messenger Adapter

Messenger API support may be added after V1.5 through a separate platform adapter. Core inquiry and pricing behavior should remain platform-neutral.

## Possible Future Integrations

Future work may consider richer owner workflows or external booking references, but these are not V1.5 commitments.

Booking.com API is not part of V1.5.

