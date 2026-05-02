# homestay-night-concierge

LINE-first homestay night inquiry and message concierge system.

This project will be built gradually. The initial version of the repository contains documentation and tenant configuration only. It does not implement FastAPI routes, LINE webhook handling, database code, or business logic yet.

## V1.5 Direction

V1.5 is focused on preliminary inquiry handling:

- Future LINE webhook support.
- Guest inquiry parsing for dates, nights, adults, children, infants, and pets.
- Conservative preliminary whole-house pricing.
- Owner LINE push notification in future implementation.
- Owner slash commands in future implementation.
- Tenant-aware data design.
- Messenger-ready architecture without Messenger API implementation.

## Product Rules

The system must never guarantee availability, confirm a booking, reserve a room automatically, or process payment/deposit.

Every preliminary quote must tell guests that actual availability and final price will be confirmed by homestay staff.

## Current Tenant

- Slug: `zhen123-house`
- Name: 枕123民宿 / 枕壹貳參
- Address: 宜蘭縣員山鄉枕山十二路123號
- Timezone: `Asia/Taipei`
- Config: `data/tenants/zhen123-house/config.json`

## Documentation

- `docs/requirements.md`
- `docs/architecture.md`
- `docs/pricing_rules.md`
- `docs/inquiry_policy.md`
- `docs/limitations.md`
- `docs/v2_roadmap.md`

