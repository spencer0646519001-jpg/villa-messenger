# Limitations

## Current Task

This first task creates documentation and tenant configuration only. It does not implement routes, webhook handling, database code, or business logic.

## V1.5 Limitations

V1.5 is preliminary quote and inquiry handling only.

Not included in V1.5:

- Guaranteed availability.
- Booking confirmation.
- Automatic room reservation.
- Payment processing.
- Deposit processing.
- Google Calendar availability check.
- Booking.com API.
- Messenger API.
- Web UI.
- AI implementation.

## Availability

The system must not represent availability as confirmed. Even when a preliminary quote can be calculated, staff must confirm actual availability and final price.

## Pricing

Pricing is conservative and policy-based. Special dates must come from tenant configuration in V1.5. Taiwan holiday API support is V2.

Cases requiring staff confirmation include:

- More than 16 guests.
- Children or infants.
- Missing pet count.
- BBQ requests.
- Special requests outside configured policy.
- Any ambiguous or incomplete inquiry.

## Messenger

Messenger can use Meta Business Suite native auto-replies for now. Future Messenger support should be implemented as a separate adapter without changing core inquiry or pricing services.

