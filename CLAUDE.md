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
3. 實作後 Spencer 會自己測試並 commit

不要一接到任務就直接改 code。尤其牽涉到護城河、狀態機、報價、意圖判斷這些核心
邏輯的改動,務必先說明設計再動手。小的、明顯無風險的改動可以直接做,但仍要說明
做了什麼。

## 🔥 當前兩大優先問題(上線後真實回報,優先於其他一切 TODO)

這兩個都是「系統插進來但幫倒忙」類型的問題 —— 不是程式壞掉,而是「系統該不該
在此刻講話」的判斷不夠聰明。**請先把設計想清楚、跟 Spencer 討論確認,再實作。**

### 問題 1:23:00 排程開機打斷進行中的人工對話

**現象:** 民宿主人(媽媽)白天/晚上正在跟某位客人一來一往手動聊天,一到 23:00
系統自動開機接手,突然插進來自動回覆同一位客人。媽媽和客人雙方都覺得莫名其妙。

**核心缺口:** 系統只用「時間」決定要不要接手,沒有「這位客人正在跟真人對話中」
的判斷。

**可能方向(尚未定案,需設計):**
- 偵測近期是否有 owner 手動回覆過該客人 → 若有,該對話豁免自動接手
- 加入 per-conversation 的「人工接管」旗標 + 冷卻時間
- 開機時只對「新進線客人」生效,不介入既有對話

### 問題 2:FAQ 關鍵字劫持空房詢問的意圖

**真實案例:** 客人問「您好,請問8/15是否還可以包棟嗎?人數9位,謝謝」,系統卻回了
「包棟」的 FAQ 名詞解釋(「枕123是一次只接待一組客人的包棟民宿...」),完全沒回答
8/15 有沒有空房。媽媽只好手動補回「不好意思8/15滿房了」。

**核心缺口:** 訊息同時含日期(8/15)+ 人數(9位)+ FAQ 關鍵字(包棟),意圖判斷被
FAQ 關鍵字搶走,沒有走空房檢查路徑。客人用「包棟」是當動詞(= 想訂整棟),不是在
問「包棟是什麼」。

**方向:** 當訊息已具備訂房要素(日期或人數)時,空房/報價路徑應優先於 FAQ 關鍵字
比對(或至少兩者都回應)。

**注意:** 過去曾修過一次類似的意圖分類問題(用 FAQ topic keywords 當 guard),
但此案例仍漏接 —— 需重新檢視現有 guard 邏輯到底怎麼判斷的,不要假設它不存在。

### 這兩個問題解決後
Spencer 要求把兩者一起整理成一份 case study。

## 部署現況(已上線)

- **平台:** DigitalOcean Droplet(Ubuntu 24.04,新加坡),Docker Compose 部署
- **對外:** `villa.<domain>` 子網域,Caddy 反向代理(系統套件版)+ HTTPS
- **host port 8002**(container 內 8000),SQLite volume `villa_sqlite`(掛 `/data`)
- **Compose 專案名:** `villa-messenger`
- 詳細部署步驟與上線踩過的坑,見 `docs/deployment.md`(6 個坑 + 官方帳號切換流程)
- `.env` 與 `secrets/service-account.json` 不進版控,只在伺服器上手動管理

### 部署相關的兩個已知技術債(TODO)
- `scripts/seed_sandbox.py` / `scripts/add_owner.py` 硬編碼相對路徑
  `data/homestay.db`,應改讀 `settings.database_path`(否則容器內執行會寫錯位置)
- `app/main.py` 缺 `logging.basicConfig()`,導致背景任務的 log 在容器中被靜音;
  目前靠 `docker-compose.yml` 的 `--log-level debug` 治標,根本解是在程式內設好 logging

## 技術棧

FastAPI + uvicorn、SQLite、LINE Messaging API、OpenRouter(LLM)、
Google Calendar API(空房檢查)。多租戶架構(`tenants` / `tenant_channels` /
`tenant_owners` 等表)。

## LLM 設定要點

- 主力:DeepSeek V4 Flash,走 OpenRouter preset `@preset/deepseek`
- Fallback:GPT-4o-mini(`openai/gpt-4o-mini`)
- **主力與 fallback 都走同一把 `OPENROUTER_API_KEY`,無 Azure**
- FallbackLLMProvider:primary 失敗 → fallback → rule-based

## 測試

完整測試套件,用 pytest。任何改動後務必跑測試確認全綠。護城河相關邏輯改動時尤其
要確認既有測試沒被破壞。

## 其他非阻斷 TODO(優先度低於上面兩大問題)

- Owner 推播加「跳轉到該客人對話」的 LINE deep link(需查 LINE 是否支援用 userId
  開啟 1:1 對話,如 `line://ti/...`)