# Requirements

## Project

`homestay-night-concierge` is a LINE-first homestay night inquiry and message concierge system.

The system helps homestay staff handle guest inquiries, prepare conservative preliminary quotes, and notify owners for follow-up. It must stay tenant-aware from the beginning so the same codebase can support any number of homestays.

## V1.5 Scope

V1.5 is preliminary inquiry handling only.

- Future LINE webhook support.
- Guest inquiry parsing for:
  - check-in date
  - checkout date
  - number of nights
  - adults
  - children
  - infants
  - pets
- Conservative preliminary pricing.
- Owner LINE push notification in future implementation.
- Owner slash commands in future implementation.
- Tenant-aware data design.
- Messenger-ready architecture without Messenger API implementation.

## Out Of Scope For V1.5

- FastAPI routes in this documentation task.
- LINE webhook implementation in this documentation task.
- Database code in this documentation task.
- Business logic in this documentation task.
- AI implementation.
- Payment or deposit processing.
- Web UI.
- Google Calendar availability checks.
- Booking.com API.
- Messenger API.

Google Calendar availability is planned for V2. Booking.com API integration is not part of V1.5.

## Hard Product Rules

The system must never:

- Guarantee room availability.
- Say a booking is confirmed.
- Reserve a room automatically.
- Process payment or deposit.

Every preliminary quote must include:

> 實際是否有空房與最終價格，仍會由民宿人員和您確認。

## Tenant Requirements

All future data access must be tenant-aware. Tenant identity must be explicit when reading configuration, pricing, inquiries, owner settings, booking links, or future external calendar data.

The system must support any number of tenants. It must not assume only `zhen123-house` or a fixed second tenant.

## Platform Requirements

LINE is the first messaging platform for V1.5 work. Core services must remain platform-neutral, so LINE-specific concepts must stay in adapter or API-layer code.

Messenger support should be possible later through a separate adapter. V1.5 does not implement Messenger API; Messenger may use Meta Business Suite native auto-replies for now.

