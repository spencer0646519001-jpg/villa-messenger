# Architecture

Last reviewed against the code: 2026-09-28.

## Principles

- Keep the backend architecture clean.
- Keep the API layer small.
- Keep core services platform-neutral.
- Keep data access tenant-aware.
- Keep LINE and future Messenger details out of core domain logic.
- Avoid over-engineering.

## Layers

```
app/api/          transport only: signature check, tenant resolution, dedupe,
                  reply/push sending, owner slash commands, background pipeline
app/adapters/     platform payload <-> domain objects (LINE, LLM providers)
app/services/     orchestration; owns the order things happen in
app/domain/       pure rules: parsing, pricing, gates, FAQ, reply templates
app/clients/      outbound I/O boundaries (Google Calendar, LINE send,
                  Taiwan holiday calendar)
app/repositories/ SQLite schema and per-table repositories
```

The API layer holds no pricing rules, inquiry policy, or parsing decisions.
Customer-visible wording lives only in `app/domain/reply_templates.py` and
`app/domain/reply_text.py`.

### Import discipline

Stated in each module's own docstring. **Not enforced by a test** — if this
starts mattering more, an import-linting test is the obvious next step.

| Module | May import | May not import |
| --- | --- | --- |
| `InquiryService` | stdlib, pydantic, `app.domain.*`, `app.schemas`, `app.services.availability_service` | `app.repositories`, `app.api`, `app.adapters`, `app.clients` |
| `ConversationReplyComposer` | same idea — receives the state row as a plain dict | `app.repositories` |
| `ConversationStateService` | `app.repositories` (it owns state persistence) | `app.api`, `app.adapters` |

Services that must reach storage take a repository *instance* or a loader
callable injected by `line_webhook_routes`, never a module-level import of the
repository layer.

## Platform Boundary

LINE-specific details live in `app/adapters/line_adapter.py` and
`app/adapters/line_signature.py`: webhook signature validation, reply tokens,
LINE user IDs, payload shapes. The adapter produces a platform-neutral
`InboundMessage` (`app/schemas.py`) carrying tenant id/slug/timezone, platform,
platform user id, text, and the message timestamp.

Core services consume `InboundMessage` and return platform-neutral results
(`InquiryDecision`, `ComposedReply`).

`app/adapters/messenger_adapter.py` is a stub. Messenger is not implemented;
Meta Business Suite canned replies cover it for now.

## Tenant Boundary

Every read and write of tenant data is scoped by `tenant_id`. Tables carrying
it: `tenant_channels`, `tenant_owners`, `processed_webhook_events`, `messages`,
`inquiries`, `tenant_operation_state`, `conversation_manual_holds`,
`conversation_states`.

Tenant configuration lives in `data/tenants/<slug>/config.json` and is reached
through `app/services/tenant_config_loaders.py`, which bridges `tenant_id` to
slug via the `tenants` table. Loaders read the file on every call; there is no
config cache.

**One deliberate exception:** `holiday_calendar_cache` is *not* tenant-scoped.
It holds the national government calendar, which is identical for every tenant.
Per-tenant overrides still live in that tenant's own `special_dates` block.

## Owner Commands

Implemented in `line_webhook_routes._handle_owner_command`, checked before any
guest handling. A message counts as a command only when it starts with `/`
**and** the sender is an active owner row for that tenant; anything else falls
through to the guest pipeline untouched.

| Command | Effect |
| --- | --- |
| `/開機` `/關機` | Manual on/off override, expiring at the next auto-schedule boundary |
| `/狀態` | Current effective mode and why |
| `/紀錄` | Recent messages in the tenant-local night window |
| `/待回覆` | Unhandled items, and closes them once shown |
| `/<customer display name>` | Toggle a per-customer manual pause (handoff Layer 1). A trailing number picks from a prior ambiguous-name reply |

## External Integrations

| Integration | Status |
| --- | --- |
| LINE Messaging API | In production |
| OpenRouter-compatible LLM | In production, narrow scope (see below) |
| Google Calendar availability | Shipped in V1.5, behind `GOOGLE_CALENDAR_AVAILABILITY_ENABLED` plus the tenant's `google_calendar.v1_5_enabled`. Per-tenant booking keyword; `zhen123-house` uses `枕` |
| Taiwan holiday calendar | Added 2026-09. Public CDN, one JSON per year, cached in SQLite |
| Messenger Platform API | Not implemented |
| Booking.com API | Not planned for V1.5 |

### LLM scope

The LLM only produces structured parse fields and single-turn clarification
signals. It never decides prices, availability, state transitions, FAQ routing,
owner notifications, or customer-visible wording. Six trigger types live in
`app/domain/llm_fallback.py` (date translation, intent judgment, FAQ/booking
collision, state continuation, BBQ ambiguity, unclassified inquiry). Any
failure — timeout, bad JSON, provider error, exhausted fallback — returns the
rule-parser result unchanged.

Dates the LLM returns are additionally rejected when they have already passed,
so the rule layer stays authoritative for year resolution.

## 完整資料流

```
LINE webhook POST
  ↓
簽章驗證 → destination 解析 channel → tenant
  ↓
先回 200 OK,再用 FastAPI BackgroundTasks 跑 _run_pipeline
  ↓
每個 event:
  ├─ 去重(processed_webhook_events;送出失敗會 rollback 這筆讓 LINE 重送)
  │
  ├─ LineAdapter → InboundMessage
  │
  ├─ 主人指令?(「/」開頭 且 寄件者是該 tenant 的 active owner)
  │    └─ 是 → 處理指令並結束,不進客人流程
  │
  ├─ 抓客人 LINE 顯示名稱(供主人推播與 /<名稱> 接管指令使用)
  │
  ├─ InquiryService.handle_message
  │    ├─ UrgencyDetector(沒水/瓦斯/停電…)
  │    │    └─ 命中 → 推播主人,不自動回任何安撫訊息 → 結束
  │    │
  │    ├─ parse_inquiry(基準日 = 訊息時間戳轉租戶當地時區)
  │    │    └─ 日期、人數、寵物、烤肉、房數、意圖、FAQ topic
  │    │
  │    ├─ 系統狀態 = on / off(排程) / paused_by_owner(單一客人接管)
  │    │    └─ 非 on → 只記錄,不回覆不推播 → 結束
  │    │
  │    ├─ llm_fallback_parse(TYPE_1/2/3/5/6,失敗一律退回規則結果)
  │    │
  │    ├─ availability_probe(只有入住日沒退房日時,假設住一晚「僅供查空房」,
  │    │    不寫入 slot、不進報價)
  │    │
  │    ├─ 非報價意圖 → 非詢價處理(FAQ 推播 / 未分類推播)
  │    │
  │    └─ 報價意圖:
  │         ├─ probe 顯示客滿 → 客滿模板 + 推播主人
  │         ├─ 缺欄位 / 需要澄清 → 補資料模板
  │         └─ _handle_pricing:
  │              ├─ room gate(缺房數 → 問;1 房或超過 16 人 → 轉人工;
  │              │             選的房數住不下 → 建議加房)
  │              ├─ holiday gate ← 行程觸及的年份都有行事曆嗎?
  │              │    └─ 否 → 不報價、轉人工、推播主人
  │              │       (「查不到假日」不可以當成「沒有假日」)
  │              ├─ calculate_price
  │              └─ availability gate(客滿 / API 失敗 / 可報價)
  │
  ├─ MessagePersistenceService(messages + inquiries 單一 transaction)
  │
  ├─ ConversationStateService.record(STAGE B:把本輪 slot 併進該客人的
  │    active state;只記錄,不決定回什麼)
  │
  ├─ 離題判斷(TYPE_4):open state 下訊息明顯離題時問 LLM 是否仍在同一段對話,
  │    回 False 才改寫回覆(只會收斂行為,不會放寬)
  │    └─ 否則 → ConversationReplyComposer.compose(STAGE C)
  │         ├─ 緊急 / off:沿用 decision
  │         ├─ NON_PRICEABLE FAQ topic 壓過報價意圖
  │         ├─ tier-1 FAQ 直答(排除 checkout 與帶價格關鍵字的訊息)
  │         ├─ off 期間累積過久 → 請客人重新確認
  │         ├─ early availability gate
  │         ├─ 依「累積後的 state」算缺什麼 → 補資料 / room gate
  │         └─ _quote_for_state(同樣先過 holiday gate 再 calculate_price)
  │
  ├─ 送出客人回覆(兩次重試);失敗 → rollback 去重紀錄讓 LINE 重送
  ├─ 送出主人推播
  └─ 完成則標記 state completed / 清除 reconfirm 旗標
```

單輪(`InquiryService`)與多輪(`ConversationReplyComposer`)是兩條會各自呼叫
`calculate_price` 的路徑,所以 room gate 與 holiday gate 兩邊都要接,不能只接一邊。

## Background Work

`app/main.py` 的 lifespan 起兩個背景任務:

- **夜間彙整輪詢** — 每 5 分鐘跑一次 `run_nightly_digest_check`,讓租戶的
  `auto_on_start_time` 邊界不需要 per-tenant cron。本身對「租戶當地日」是
  idempotent,所以輪詢粒度只影響延遲上限,不影響次數。
- **行事曆預熱** — 開機時抓今年與明年的假日行事曆進快取,避免年初第一位客人
  卡在 HTTP 抓取。失敗不影響啟動,per-stay 的 gate 會自己重試。

兩者都不是持久化佇列。webhook 處理同樣跑在同一個 process 的 `BackgroundTasks`
裡,部署時要給足 graceful shutdown 時間(見 `docs/deployment.md`)。

## Storage

SQLite,schema 在 `app/repositories/schema.sql`,由 `init_db()` 套用。
**不會在 uvicorn 啟動時自動執行**,每次 schema 變動都要手動跑一次。

新表靠 `CREATE TABLE IF NOT EXISTS` 自動建立;**新欄位不會**,必須在 `init_db()`
裡手寫一行對應的 `_ensure_column`(見 `docs/deployment.md` 坑 7)。

| 表 | 用途 |
| --- | --- |
| `tenants` / `tenant_channels` / `tenant_owners` | 多租戶身份、LINE channel 對應、主人綁定 |
| `processed_webhook_events` | webhook 去重 |
| `messages` / `inquiries` | 每則訊息與每筆詢價決策 |
| `conversation_states` | 多輪 slot 累積(每位客人至多一筆 in_progress) |
| `tenant_operation_state` | On/Off 排程與手動覆寫 |
| `conversation_manual_holds` | per-customer 人工接管暫停 |
| `holiday_calendar_cache` | 政府辦公日曆表原始 payload,一年一列,不分租戶 |
| `contacts` | 有 repository 與測試,尚未接進流程 |
| `reservations` / `conversation_links` | **建了但完全沒被引用**,V2 預留 |

## 相關文件

- [pricing_rules.md](pricing_rules.md) — 報價規則的權威來源
- [limitations.md](limitations.md) — 刻意不做的事
- [operation_modes.md](operation_modes.md) — On/Off 與接管
- [llm_fallback_design_v2.md](llm_fallback_design_v2.md) — LLM 觸發設計
- [deployment.md](deployment.md) — 部署與上線踩坑
