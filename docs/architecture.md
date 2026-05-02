# Architecture

## Principles

- Keep the backend architecture clean.
- Keep the API layer small.
- Keep core services platform-neutral.
- Keep data access tenant-aware.
- Keep LINE and future Messenger details out of core domain logic.
- Avoid over-engineering.

## Future Backend Shape

This repository starts with documentation and tenant configuration only. Future implementation should use a small number of clear boundaries:

- API/adapters: receive platform events, validate transport payloads, and call application services.
- Application services: coordinate inquiry parsing, quote preparation, owner notification, and command handling.
- Domain/policy modules: hold platform-neutral rules for inquiries, pricing, stay policy, and safety wording.
- Tenant configuration: load tenant-specific facts such as pricing, policies, phone numbers, and source pages.
- Data access: read and write tenant-scoped data only.

The API layer should not contain pricing rules, command business behavior, inquiry policy, or platform-neutral parsing decisions.

## Platform Boundary

LINE-specific details belong in a future LINE adapter. Examples include webhook signature validation, LINE user IDs, reply tokens, push messages, and LINE message payload formats.

Core services should receive normalized platform-neutral input, such as guest message text, tenant slug, sender role, and message timestamp. Core services should return platform-neutral results, such as preliminary quote text, owner notification content, or command output.

Messenger should follow the same adapter pattern later. V1.5 does not implement Messenger API.

## Tenant Boundary

Tenant slug must be part of every future operation that reads or writes tenant data. This includes:

- Tenant configuration.
- Pricing and policy lookup.
- Inquiries.
- Owner bindings.
- Owner commands.
- Future booking references.
- Future calendar availability checks.

No future query should rely on global data without tenant scope.

Future repository methods should require `tenant_id` or `TenantContext` explicitly.

## Owner Command Boundary

Owner slash commands are planned for future tasks. Command handling should be separate from guest inquiry handling.

- Only tenant owners may use slash commands.
- Owner messages that do not start with `/` must not be intercepted.
- Guest messages that start with `/` must not expose owner command behavior.

## Future External Integrations

Google Calendar availability checks are V2. The future design should allow each tenant to define its own calendar booking keyword. For `zhen123-house`, the keyword is `枕123`.

Booking.com API is not part of V1.5.
