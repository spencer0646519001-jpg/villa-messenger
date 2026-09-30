# CLAUDE.md — villa_messenger 專案指南

> 這是 Claude Code 的專案常駐記憶。每次 session 啟動時閱讀,以取得專案背景、
> 工作流程與當前優先事項。

## 專案是什麼

villa_messenger 是一個 LINE 民宿自動訂房助理,服務家庭經營的民宿「枕123民宿」
(正式官方帳號已上線,約 400+ 位真實客人)。功能包含:空房查詢、多輪訂房對話、
規則式初步報價、FAQ 回覆、以及對民宿主人(owner)的通知推播。

開發者 Spencer 為單人開發。

## 🛡️ 核心架構原則:護城河(不可違反)

**LLM 只負責「模糊語意解析」與「意圖判斷」,絕不碰以下任何一項:**
- pricing(報價邏輯)
- 狀態機(conversation state)
- availability(空房判斷)
- reply_templates(所有客人看得到的文字)

**所有客人可見的回覆文字,一律來自 `reply_templates.py`。**
LLM 失敗 / 逾時 / 回傳壞 JSON 時,一律 fallback 到 rule-based 處理。

這是刻意的設計邊界。任何改動都必須守住這條線 —— 不要為了方便讓 LLM 直接生成
報價數字、狀態轉換或客人回覆文字。

## 🔄 與 Spencer 的工作流程(重要)

**日常開發交給 Claude Code,但重要 / 有風險的改動,先設計、說明、取得同意,再實作。**

具體來說:
1. 遇到需求或問題,**先分析、提出設計方案**(講清楚「要怎麼改、為什麼、影響範圍」)
2. **等 Spencer 確認**後,才開始寫 code
3. 可以在 **feature branch** 上 commit(codex-review 的 hook 靠 commit 觸發),
   但**不得碰 main、不得 push**,除非 Spencer 明確指示 —— merge 與 push 由他決定

不要一接到任務就直接改 code。尤其牽涉到護城河、狀態機、報價、意圖判斷這些核心
邏輯的改動,務必先說明設計再動手。小的、明顯無風險的改動可以直接做,但仍要說明
做了什麼。

## ✅ 已解決的兩大優先問題(上線後真實回報)

這兩個都曾是「系統插進來但幫倒忙」類型的問題 —— 不是程式壞掉,而是「系統該不該
在此刻講話」的判斷不夠聰明。兩者皆已設計確認並實作完成,詳細分析與修法見
`docs/case_study_intent_and_handoff.md`。

- **問題 1:23:00 排程開機打斷進行中的人工對話** —— commit `27bc622`(2026-07-27,
  邊界 bug 修正於 2026-07-29)。加入 per-customer 手動接管暫停(`/<客人顯示名稱>`)、
  舊資料軟性提醒、關機期間漏接彙整推播。
- **問題 2:FAQ 關鍵字劫持空房詢問的意圖(包棟案例)** —— commit `53113fd`
  (2026-07-27)。product/policy topic 分類 + LLM collision 判斷 + 規則兜底,讓
  已具備日期/人數等訂房要素的訊息不再被 FAQ 關鍵字搶走。

兩者皆已跑過完整測試套件(956 綠)。問題 1 的 Layer 2(舊資料重新確認提示)尚未
在真實線上環境人工驗證過,其餘已於本機/線上驗證。

## ✅ 第三個真實事故:春節報價少一半(2026-09,已修)

客人問「明年 2/6-8」,系統報 NT$29,000,主人報 NT$60,000。**程式邏輯沒壞,壞的是
「缺漏資料被當成有效資料」** —— 查不到假日就當平常日,沒寫年份就當今年。兩者都不
報錯、不留 log、回覆看起來完全正常,只有金額是錯的。

三個根因,詳見 `docs/case_study_holiday_pricing_and_year_inference_2026-09.md`:

1. 房型組合記法「開 4/4/2」被當成 4 月 4 日 → 拿過去的日期去查行事曆
2. 規則層完全不懂年份(「明年」被忽略、跨年訂單一律被擋)。那次年份之所以對,
   是因為訊息裡有「2台車」意外觸發 LLM 順手解對 —— **靠運氣,且違反護城河精神**
3. `special_dates` 只有 2026,2027 一片空白,而查不到就靜默退回平日價

**新增的原則:寧可不報價,也不要靜默報錯價。** 行事曆現在自動抓政府辦公日曆表並
快取(`app/domain/holiday_calendar.py` 推導 + `app/domain/holiday_gate.py` 把關),
行程觸及的年份拿不到資料就不報價、轉人工、推播主人。

定價後續(2026-09-30 Spencer 決定):改回官網現行價目表(`報價-20260908更改.jpg`)——
春節依房數 25,000 / 28,000 / 31,000(取消 9-28 的「一律 30,000」),2 房平日 10,000,
並**取消連住折扣**。折扣改由 tenant config 的 `pricing.long_stay_discount` 控制,沒寫就是 0。

## ✅ 第四個真實事故:房數回答被解析錯(2026-09-30,已修)

客人 8大4小 → 系統問開幾間房 → 「全部」(沒解析到,重問)→ 「4人2間 2人2間」→
報成「4 大 4 小、開 2 間房」。規則**很有自信地解析錯**(取第一個「2間」、光禿
「4人」蓋掉成人數),所以「規則失敗才叫 LLM」救不了。

修法:對話在等房數時,只有單純回答(「4」「開3房」)交給規則,其他說法一律交
LLM(`TYPE_7_ROOM_COUNT_ANSWER`)**只判斷房數**,且必須落在 1..total_rooms;LLM
沒答案時規則遇到多組房數就重問。同一則訊息有提房/間但沒有大/小標籤的「N人」
視為房型,不蓋掉已存人數。

### ⚠️ 部署待辦(Spencer 尚未完成,完成後刪掉這節)
程式已合進 main 並 push(2026-09-28),但**線上還沒部署**。部署時兩件事:

1. **跑一次 `init_db()`** —— 線上既有資料庫才會有 `holiday_calendar_cache` 這張表
   (與 `wants_bbq` 那次同一個坑)。流程照 `docs/deployment.md`「更新既有服務的
   標準流程(含 schema 變更)」:先 build 新 image,再用 `run --rm` 跑 migration。
2. **確認容器能對外 HTTPS 連到 `cdn.jsdelivr.net`** —— 行事曆從那裡抓。連不到
   不會報錯價,但所有需要行事曆的詢價都會轉人工。驗證:部署後看 log 有沒有
   `Holiday calendar ready for [2026, 2027]`;若是 `prewarm incomplete` 就是連不到。

下一個 session 開始時,如果這節還在,主動提醒 Spencer。

## 部署現況(已上線)

- **平台:** DigitalOcean Droplet(Ubuntu 24.04,新加坡),Docker Compose 部署
- **對外:** `villa.<domain>` 子網域,Caddy 反向代理(系統套件版)+ HTTPS
- **host port 8002**(container 內 8000),SQLite volume `villa_sqlite`(掛 `/data`)
- **Compose 專案名:** `villa-messenger`
- 詳細部署步驟與上線踩過的坑,見 `docs/deployment.md`(9 個坑 + 官方帳號切換流程)
- `.env` 與 `secrets/service-account.json` 不進版控,只在伺服器上手動管理

### 部署相關的已知技術債(TODO)
- `scripts/seed_sandbox.py` / `scripts/add_owner.py` 硬編碼相對路徑
  `data/homestay.db`,應改讀 `settings.database_path`(否則容器內執行會寫錯位置)
- ~~`app/main.py` 缺 `logging.basicConfig()`~~ —— 已完成,不需再處理

## 技術棧

FastAPI + uvicorn、SQLite、LINE Messaging API、OpenRouter(LLM)、
Google Calendar API(空房檢查)、台灣政府辦公日曆表(假日定價,公開 CDN,
需要對外 HTTPS)。多租戶架構(`tenants` / `tenant_channels` / `tenant_owners`
等表)。架構現況見 `docs/architecture.md`。

## LLM 設定要點

- 主力:DeepSeek V4 Flash,走 OpenRouter preset `@preset/deepseek`
- Fallback:GPT-4o-mini(`openai/gpt-4o-mini`)
- **主力與 fallback 都走同一把 `OPENROUTER_API_KEY`,無 Azure**
- FallbackLLMProvider:primary 失敗 → fallback → rule-based

## 測試

完整測試套件,用 pytest。任何改動後務必跑測試確認全綠。護城河相關邏輯改動時尤其
要確認既有測試沒被破壞。

## 其他非阻斷 TODO

- Owner 推播加「跳轉到該客人對話」的 LINE deep link(需查 LINE 是否支援用 userId
  開啟 1:1 對話,如 `line://ti/...`)
- 複數意圖處理(一則訊息同時問訂房與政策,例如「8/15 包棟可以帶寵物嗎 9人」)
  —— 地基已備妥但流程未做,設計題見 `memory.md` 第五章