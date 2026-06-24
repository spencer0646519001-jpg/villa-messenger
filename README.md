# homestay-night-concierge

LINE-first homestay night inquiry and message concierge system.

This project will be built gradually. The initial version of the repository contains documentation, tenant configuration, SQLite schema setup, and a small FastAPI health surface. It does not implement LINE webhook handling or business logic yet.

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

## Tenant Configuration

Tenant config lives under `data/tenants/{tenant_slug}/config.json`.
V1.5 uses file-based tenant config before DB-backed tenant management.

## Persistence

SQLite is used for V1.5 local/demo persistence, with PostgreSQL as the future SaaS/V3 direction. Local DB files are ignored by git.

## Documentation

- `docs/requirements.md`
- `docs/architecture.md`
- `docs/pricing_rules.md`
- `docs/inquiry_policy.md`
- `docs/limitations.md`
- `docs/v2_roadmap.md`

## Local Development

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run tests:

```powershell
python -m pytest
```

Run the app:

```powershell
uvicorn app.main:app --reload
```

## Deployment Checklist

- LINE webhook work after signature verification runs through FastAPI
  `BackgroundTasks`. These tasks execute in the same worker process after the
  200 response is sent; they are not a durable queue.
- Configure the server/process manager with enough graceful shutdown time for
  webhook background tasks to finish before a worker is killed.
