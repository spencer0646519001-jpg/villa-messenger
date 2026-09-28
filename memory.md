# villa_messenger — 專案記憶檔

> 用途:給 Claude Code(或任何新的協作對象)快速理解這個專案的來龍去脈。
> 最後更新:2026-09-28
>
> **閱讀順序建議**:先讀「零、專案是什麼」與「一、護城河原則」,再看
> 「五、未解決的問題」。中間的歷史章節可以之後再看。
>
> ⚠️ 這份與 `CLAUDE.md` 有相當程度重疊。`CLAUDE.md` 是 session 啟動時必讀的
> 精簡版;這份是完整的來龍去脈與歷史教訓。兩份都要更新,不然會各說各話。

---

## 零、專案是什麼

**villa_messenger** —— 一個 LINE 上的民宿自動訂房助理,服務對象是家族經營的民宿
「枕123民宿」(tenant 代號 `zhen123-house`)。

**它做的事:**
- 接收客人在 LINE 上的訊息
- 判斷客人想問什麼(查空房、問價格、問設施規定…)
- 多輪對話收集資料(入住日、退房日、人數、房數)
- 查 Google Calendar 確認有沒有空房
- 依房數報價
- 通知民宿主人

**相關的人:**
- **Spencer** — 開發者(獨立開發,這是他第二個專案)
- **Spencer 的媽媽** — 民宿主人,系統的主要操作者,也是 owner 通知的接收者
- **Spencer 的姊妹** — 也是 owner 通知接收者
- **Spencer 的太太** — 曾用全新 LINE 帳號當測試客人,協助隔離 bug

**目前狀態:已上線正式營運**,接的是真實的 LINE 官方帳號(400+ 真實客人)。

---

## 一、🛡️ 護城河原則(最重要,不可違反)

這是整個專案的核心設計約束,**任何改動都不能違反**:

**LLM 只做兩件事:模糊語意解析、意圖判斷。**

**LLM 絕對不可以碰的四項:**
1. 計算價格(pricing)
2. 判斷或轉移 conversation state
3. 判斷 availability(空房結果)
4. 產生任何客人可見的回覆文字

所有客人看得到的字,一律來自 `reply_templates.py` / `reply_text` 的既有模板。

**LLM 失敗、逾時、回傳壞 JSON、或未啟用時,一律要有規則式 fallback。**
系統行為仍須合理,不能整個掛掉或無回應。

---

## 二、工作方式(Spencer 的明確要求)

**原本的流程:** Claude 寫規格與 review → Codex 實作 → Spencer 測試並 commit

**2026-07-27 起調整為:** 日常實作工作移到 Claude Code(因為 chat 的 context 長度
限制),設計討論仍在 claude.ai chat 進行(因為有持久記憶)。`CLAUDE.md` 是兩邊
之間的橋樑。

**Spencer 對協作對象的具體要求(務必遵守):**

1. **動 code 之前,先用簡單易懂的方式說明你打算怎麼做、為什麼、有哪些取捨。**
   避免不必要的術語堆砌,像跟不熟悉這個系統的人解釋一樣。
2. **等 Spencer 理解並同意後,才開始實作。**
3. 如果設計有多個可行方向,**列出來讓 Spencer 選**,不要自己決定一個就做下去。
4. **可以在 feature branch 上 commit,但不得碰 main、不得 push**(2026-09-28
   Spencer 拍板)。原本是「不要主動 commit」,改的理由:`codex-review` skill 靠
   「真的產生 commit」觸發 hook 叫 Codex review,不 commit 就拿不到第二個 agent
   的意見。merge 到 main 與 push 由 Spencer 自己測試(含真機)後決定,或由他
   明確指示。
5. **實作中若發現規格與現實衝突,先停下回報**,不要自行決定並默默改變設計方向。
   (有過先例:單一日期推定 checkout 若也用於報價會誤導客人,Codex 有回報,
   規格因此修正,這是正確的做法。)
6. Spencer 偏好繁體中文、小學生等級的清楚說明。

**Spencer 對這套流程的態度(2026-07-27 討論):**
他明確表示偏好「先討論、後實作」而非直接丟給工具跑,認為這是刻意的判斷力培養,
不是效率損失。他觀察到身邊的工程師傾向於「不理解就一直迭代」。這個做法在他目前
階段是合適的;協作對象應該在觀察到「可以轉向更自動化」的訊號時主動提出。

---

## 三、時間軸:重要事件

### 2026-07-02 前後 — LLM 整合與定價邏輯

- 註冊 OpenRouter API key,DeepSeek 為主、Qwen 為備
- 跑了真實模型評測:兩個模型都 80% 通過率,DeepSeek 快 25%(7.3s vs 9.7s)
- 實作 EVAL-002 房數定價邏輯:
  - 2 房 = 8 人價、3 房 = 10 人價、4 房 = 12 人價
  - 只有 4 房全包才能加床,超過 12 人每人 +$1,000,上限 16 人
  - **1 房的請求一律轉人工**
  - 房產配置:2 間 4 人房 + 2 間 2 人房;定價純看房數,不看房型
- 修了四個真機測試發現的 bug(詳見「四、歷史 bug」)
- 測試數:787 → 851

### 2026-07-05 前後 — UNIQUE 約束 bug、LLM 供應商安全、Google Calendar

- **修掉 stale-state UNIQUE 約束 bug**(見「四、歷史 bug」第 4 項),測試 858 全綠
- **OpenRouter 供應商安全整頓**:
  - 發現 Qwen3.6 Flash 只有阿里雲供應商 → **完全移除 Qwen**
  - DeepSeek V4 Flash 的 Standard routing 可能打到百度千帆
  - 建立 OpenRouter preset `deepseek`:`ignore: ["baidu", "alibaba"]`、
    `allow_fallbacks: true`、`data_collection: "deny"`
  - 加入 GPT-4o-mini 作為封閉源對照組與備援
- **建立 FallbackLLMProvider 包裝層**:主要供應商失敗(timeout / http_error /
  parse_error)自動切換到備援;兩者都失敗才退回規則式。已在正式環境驗證。
- **完成 Google Calendar 空房檢查**(上線的 blocker):
  - 共用 `availability_gate.py`,單則訊息路徑(InquiryService)與多輪路徑
    (ConversationReplyComposer)使用同一套判斷邏輯
  - 發現並修掉「單則訊息被報價兩次」的 bug(用既有 `completes_conversation_state` 旗標)
  - 空房檢查時機往前移:只要 checkin + checkout 都有值,且**本則訊息帶了日期 slot**,
    就立刻查空房,不等房數
  - 滿房時:停止追問、回滿房訊息、標記 state 完成、推播 owner
    (含日期區間、人數或「尚未提供」、客人 LINE `platform_user_id`)
- Spencer 提到「測試全綠但正式環境爆掉」這個現象(stale-state bug)是很好的
  X/Twitter thread 題材

### 2026-07-27 — 雲端部署 + 上線 + 兩個上線後問題

**上半場:雲端部署**

部署到既有的 DigitalOcean Droplet(Ubuntu 24.04, 新加坡, 4GB RAM),與 sched-v1、
sched-v2 並存。villa-messenger 走 port 8002、子網域 `villa.spencerailab.com`,
Caddy 反向代理,Spaceship 管 DNS。

部署過程踩到六個坑(已全部寫進 `docs/deployment.md`,見「六、部署教訓」)。

解決後:接上正式 LINE 官方帳號(400+ 真實客人)、驗證 owner 推播對真實 owner
有效、sched-v1 除役。

**下半場:兩個上線後 bug**

- **問題 2(已完成)**:FAQ 關鍵字劫持訂房意圖 —— 已修復並真機驗證,測試 886 → 907
- **問題 1(當時進行中,已於 2026-07-29 完成)**:23:00 排程開機打斷人工對話

### 2026-07-29 ~ 07-30 — 問題 1 完成 + 部署技術債

- `27bc622` **問題 1 修復**:三層防護 —— per-customer 手動接管暫停
  (`/<客人顯示名稱>`)、舊資料軟性提醒、關機期間漏接彙整推播。
  邊界 bug(paused 客人要優先於 tenant off 判斷)於 `92ce3c2` 補掉。
- `10fce41` 記下這次 schema 部署學到的三個坑(`_ensure_column` 是手動註冊制、
  build 與 migration 的先後順序、伺服器上手改會擋住 `git pull`)。
- `efa1220` 兩大優先問題的合併 case study(`docs/case_study_intent_and_handoff.md`)。
- `ef03363` 待回覆結案機制、handled 真實送達修正、digest 跨午夜與重試、同名客人選號。

### 2026-08-03 ~ 08-05 — 定型表單解析 + BBQ

- `0cba8d7` 客人回覆 LINE OA 定型表單時的一連串解析 bug:FAQ 劫持、date_parser
  的換行 label bug、寵物否定陷阱、寵物數量追問迴圈;同時把 BBQ 真正接進訂房流程
  (新增 `wants_bbq` 欄位,既有 DB 要手動跑 `init_db()`)。
- `51874a0` Codex review 抓到的 6 個問題一次修掉(digest 內容消失、BBQ 加價與
  文件不符、寵物/BBQ 改口沒清狀態、「不要」否定缺口、人數解析偷空格、
  `/Room 101` 接管指令誤判)。
- 這次之後,「**護城河相關改動落地後,找第二個 agent 獨立 review**」成為常態流程,
  不是一次性做法。

### 2026-08-23 ~ 08-24 — eval harness

- `06c9f5f` 用去識別化的真實客人對話建立 50 題 eval 資料集與 `eval/` 執行器。
- `6afb0b8` `has_pet`/`wants_bbq` 的 tri-state 計分修正(DB 欄位是 NOT NULL
  DEFAULT 0,存不下「不知道」這個狀態,是 eval 量錯地方而非程式邏輯錯)。
- `44e3fc0` 盤查標記一致性時**意外挖到真實 bug**:沒有標籤的多行表單回覆
  (`姓名/電話/8／8-8／9/6位/無寵物`)整輪資訊被漏接。
  見 `docs/case_study_eval_unlabeled_form_reply_2026-08.md`。
- `766b906` 離題判斷(TYPE_4):open state 下明顯離題的訊息問 LLM 是否仍在同一段
  對話,只會收斂行為不會放寬。

### 2026-08-27 ~ 09-02 — eval 驅動的一連串修正

eval 跑出來的失敗案例帶出好幾輪修正,每一輪都經過 Codex review:

- 意圖分類缺口、日期區間解析缺口(`eec20a8` → `ac8f084` → `e538d6f`)
- 人數範圍/概數解析(`115c28b` → `f962060`)
- `3409642` / `28f67f7` 多輪狀態保留強化(3-A 完整日期區間即開 state;
  3-B 真正的新日期+人數才 supersede 舊 state),`585d16d` 補強誤判與資料遺失
- BBQ 的「想」字辨識與否定範圍打了七輪(`10088ec` 到 `2d855fc`)——
  這次經驗直接導致後來新增 TYPE_5/TYPE_6 LLM 觸發,不再用 regex 追每一種講法
- `a3be10a` 起新增 TYPE_5(BBQ 模糊)與 TYPE_6(未分類詢問)兩個 LLM 觸發
- `ed6470e` 起修 BBQ 劫持訂房回覆、location FAQ 的「哪裡」變體

### 2026-09-28 — 春節報價少一半(第三個真實事故)

客人問「明年 2/6-8」,系統報 NT$29,000,主人報 NT$60,000。
**程式邏輯沒壞,壞的是「缺漏資料被當成有效資料」。** 三個根因:

1. 房型組合記法「開 4/4/2」被當成 4 月 4 日 → 拿七個月前的日期去查行事曆
2. 規則層完全不懂年份(「明年」被忽略、跨年訂單一律被擋)。那次年份之所以對,
   是因為訊息裡有「2台車」意外觸發 LLM 順手解對 —— **靠運氣,且違反護城河精神**
3. `special_dates` 只有 2026,2027 一片空白,而 pricing 對查不到的日期一律
   當成平常日

修法:行事曆改成自動抓政府辦公日曆表並快取;**行程觸及的年份拿不到資料就不報價、
轉人工、推播主人**。定價一併照 Spencer 決定調整(春節不分房數一律 30,000;
行程含春節或國定假日則整筆不打連住折扣)。

**這次確立的原則:寧可不報價,也不要靜默報錯價。**
完整紀錄見 `docs/case_study_holiday_pricing_and_year_inference_2026-09.md`。

---

## 四、歷史 bug 與修法(值得記住的教訓)

### 1. 裸數字房數「4」不被辨識
用 `parse_room_count_answer()` 修復,**只在系統正在等房數答案時才啟用**,
避免污染全域 parser。

### 2. 殭屍 state bug
人工轉介後沒有關閉 `conversation_state`,導致後續所有訊息(連打招呼)都被轉給
owner。修法:走既有的 `completed_state_id` → `_mark_if_complete` → `mark_completed` 鏈。

### 3. 意圖缺口(第一版)
含「可以嗎/嗎/?」的自然詢問句被歸類為 FAQ 而非查空房,繞過報價流程。
修法:用 `faq_matcher` tier-1 topic 關鍵字當守門員。
**注意:這個修法不夠完善,後來的問題 2 就是它的殘留缺口。**

### 4. stale-state UNIQUE 約束 bug ⭐ 最重要的教訓
`conversation_states` 上的 partial UNIQUE index(針對 `status='in_progress'`)
**不檢查 `expires_at`**,所以「已過期但沒被標記」的 state 會默默擋住新 state 的建立,
造成多輪對話記憶全失(客人一直被問已經回答過的問題)。

修法:
- 在 `record()` 開頭呼叫 `expire_stale_for_user()`(**scoped 到單一使用者**,
  不是整個 tenant)
- 邊界條件從 `expires_at < now` 改成 `expires_at <= now`,與 `> now` 的 active
  判斷完全互補

保留原本的 `expire_stale()` 供 tenant 層級的維護使用。

### 5. 單則訊息被報價兩次
InquiryService 與 Composer 各報一次。用既有的 `completes_conversation_state`
旗標解決。

### 6. 問題 2:FAQ 關鍵字劫持訂房意圖(2026-07-27 完成)

**真實案例**:客人問「請問8/15是否還可以包棟嗎?人數9位」→ 系統回了「包棟」是
什麼意思的名詞解釋,**沒有查空房**。

**修法(四個部分):**

- `app/domain/faq_matcher.py`:FAQ topic 分成兩類
  - **產品型**(如 `whole_house` 包棟)—— 問它 = 想訂
  - **政策型**(如寵物、烤肉)—— 問它 = 問規則
- `app/domain/inquiry_intent.py`:偵測「明確 FAQ topic + 訂房訊號(日期/人數)
  同時存在」的 collision,交給 LLM 判斷真實意圖;LLM 失敗/未啟用時規則式兜底
  (產品型 → 走訂房,政策型 → 維持回 FAQ)
- `app/services/conversation_reply_composer.py`:composer 第二層(gate3)的 FAQ
  比對也一併修正,避免劫持在這一層再次發生
- `app/domain/availability_probe.py`(新檔):只解析出單一日期(無 checkout)時,
  推定隔夜區間(checkin+1)**僅用於查空房**
  - 不寫入真正的 `checkout_date`、不影響 `missing_fields`、不進 pricing
  - 滿房 → 直接回覆(且**必須明示查詢的日期區間**如「8/15–8/16」)
  - 有空 → 維持既有行為,照常追問退房日

**真機測試後還修了文案** —— 原本「推定住一晚」的滿房訊息說法讓客人看不懂。

**⚠️ 已為複數意圖預留資料(重要)**:`parser_models.py` 新增了 matched topics
(記錄**所有**命中的 FAQ topic,不只第一個)與 LLM 多重意圖欄位,但目前只取其中
一個來用,**尚未真正支援複合回覆**。

---

## 五、未解決的問題

> 問題 1(23:00 排程開機打斷人工對話)與問題 2(FAQ 關鍵字劫持訂房意圖)
> **都已完成並上線**,原本寫在這裡的設計討論已移除 —— 留著會讓新的協作對象
> 誤以為還沒做。完整的來龍去脈見
> `docs/case_study_intent_and_handoff.md`,commit 是 `53113fd`(問題 2)與
> `27bc622` + `92ce3c2`(問題 1)。
>
> 問題 1 當時列出的「Off 期間最後一則訊息永遠沒人回」也已經解決,但走的是
> **第三條路**:不是回覆客人(那會正好撞到問題 1 要防的情境),而是開機後把
> 關機期間漏接的訊息**彙整成一則推播給主人**(`run_nightly_digest_check`,
> 每租戶每個當地日只送一次)。

### 問題 1.5:複數意圖處理(目前唯一明確待辦的設計題)

> 2026-09-28 確認仍未實作:`llm_detected_intents` 只有被寫進 log_payload,
> composer 仍然是 early return、一命中就結束。

#### 背景

客人可能在一則訊息裡問多個問題,例如:`8/15 包棟可以帶寵物嗎 9人`

**理想行為:兩個問題都回答**(空房查詢結果 + 寵物政策),而不是只回答其中一個。

#### 現況(問題 2 已預留的地基)

- `match_faq()` 目前「第一個命中即回傳」,但已有機制可取得**全部**命中的 tier1 topic
- LLM 輸出結構已支援多重意圖欄位,但目前只取其中一個
- **資料層面抓得到,但流程還不會分別處理並組合回覆**

#### 範圍限制(第一階段嚴格遵守)

**只支援「一個訂房意圖 + 一個政策型 FAQ topic」的組合。**
其他組合(多個政策型 topic、多個產品型 topic、三個以上意圖)一律退回現行單一路徑。

理由:避免做成通用組合器導致複雜度失控。

#### 護城河補充規則(重要)

**LLM 抓錯複數意圖怎麼辦?** 規則式兜底在單一意圖時是「退回規則判斷」,
但複數意圖沒有等價的保守做法。因此:

> **只有在規則層也能獨立確認兩個意圖都存在時(規則層抓到明確 FAQ topic
> **且** 抓到訂房訊號),才組合回覆;否則一律退回單一意圖路徑。**

LLM 在這裡只能「確認」規則層已經看到的東西,**不能單方面「新增」一個意圖**。

#### 需要想清楚的設計問題

1. 兩個以上的答案怎麼組合成一則回覆?順序怎麼決定(訂房 > FAQ?)
2. 如果訂房路徑需要**追問**缺的資料,而 FAQ 可以直接回答,兩者怎麼在同一則回覆
   裡共存不亂?(提示:追問通常要放最後,客人才知道要回什麼)
3. 訂房路徑會建立/更新 conversation state,FAQ 不會 —— 複合情境下狀態怎麼處理?
4. 組合後的文字仍須全部來自 `reply_templates`,**不可以讓 LLM 生成銜接語句**。
   怎麼在不引入 LLM 生成的前提下把兩段接得自然?
5. 是否要限制單則回覆長度?兩段加起來在 LINE 上會不會過長?

#### 這是架構層級的改動

現有 `InquiryIntentResult` 是單一 `inquiry_type`,classifier 與 composer 都是
early return,一命中就結束。**不要因為地基已經打好就倉促實作。**

#### 驗收標準

- `8/15 包棟可以帶寵物嗎 9人` → 回覆同時包含空房結果**與**寵物政策
- `8/15 包棟嗎 9人`(單一意圖)→ 行為與現在完全一致,不多出 FAQ 段落
- `可以帶寵物嗎`(單純 FAQ)→ 行為與現在完全一致,不多出訂房追問
- LLM 關閉時,複合案例仍能正確組合
- LLM 回傳異常時,退回單一意圖路徑
- 既有測試全綠

---

## 六、部署教訓(已寫進 `docs/deployment.md`)

> 以下是上線當天(2026-07-27)的 6 個坑。`docs/deployment.md` 目前已累積到
> **9 個**,後三個是 2026-07-29 那次 schema 部署學到的:`_ensure_column` 是
> 手動註冊制、有 schema 變更時要先 build 再用 `run --rm` 做 migration、
> 伺服器上未 commit 的手改會擋住 `git pull`。以那份文件為準。


1. **`.dockerignore` 不要整個排除 `data/`** —— 會連帶擋掉 `config.json` 進不了
   image,Google Calendar 檢查會壞。用精確的 glob(`data/*.db` 等)。
2. **多租戶架構需要 seed channel 記錄與 owner 記錄**,只跑 `init_db` 不夠。
   Seed 腳本有寫死的相對路徑;**在容器內一律用絕對路徑 `/data/homestay.db`
   搭配 heredoc Python**,不要直接跑 seed 腳本。
3. **`uvicorn` 沒有 `--log-level debug` 會吃掉所有背景任務的 log。**
   FastAPI BackgroundTasks 在回傳 200 之後才在 threadpool 跑 `_run_pipeline`,
   沒設定 logging 的話錯誤完全看不見。
4. **`docker compose restart` 不會重載 `env_file`** —— 改完 `.env` 必須用
   `up -d --force-recreate`。
5. **本機 ngrok/uvicorn 忘了關會攔截給雲端的 LINE webhook**,造成「已讀不回」
   的假象(訊息被本機吃掉了)。
6. **LINE `platform_user_id` 是 per 官方帳號 scoped 的** —— 測試帳號的 ID 跟
   正式帳號完全不同。換帳號後所有 owner 記錄都要重新 seed
   (從各 owner 發測試訊息後,由 `messages` 表撈 ID)。

### Schema migration 原則

`uvicorn app.main:app` 啟動時**不會**自動跑 `init_db()`。`_ensure_column` 只在
明確呼叫 `init_db()` 時才執行。**每次有 schema 變更的部署都需要 pre-start migration:**

```bash
PYTHONPATH=. python -c "from app.repositories.sqlite import init_db; from app.settings import settings; init_db(settings.database_path)"
```

`_ensure_column` 只處理可為 null 的欄位新增;約束、重新命名、沒有預設值的
NOT NULL 都需要正式 migration 或重建策略。

### 其他部署必辦

- **Graceful shutdown timeout** 必須設定,讓 FastAPI BackgroundTasks 在 worker
  被砍掉前跑完。這是 BackgroundTasks(非持久化佇列)做法的已知風險窗口。
- **LINE Official Account Manager 的回應模式必須設為「僅手動聊天」**
  (不能是手動 + 自動回應),否則 LINE 內建的自動回覆會打架。

---

## 七、技術環境

### 基礎設施
- DigitalOcean Droplet(Ubuntu 24.04, 新加坡, 4GB RAM)
- Docker Compose,專案名 `villa-messenger`,port 8002,volume `villa_sqlite`
- 系統 Caddy 做 HTTPS 反向代理
- Spaceship 管 DNS,網域 `spencerailab.com`,子網域 `villa.spencerailab.com`
- 並存服務:`sched-v2`(port 8001)。sched-v1 已除役。

### 資料庫
SQLite,容器內路徑 `/data/homestay.db`

### LLM 設定
- **主要**:DeepSeek V4 Flash,經 OpenRouter preset `@preset/deepseek`
  (preset 在 OpenRouter dashboard 設定:`ignore: ["baidu", "alibaba"]`、
  `allow_fallbacks: true`、`data_collection: "deny"`)
- **備援**:GPT-4o-mini 經 OpenRouter(`openai/gpt-4o-mini`)
- **兩者共用單一 `OPENROUTER_API_KEY` —— 系統裡沒有任何 Azure endpoint 或 key**
- Qwen 已完全移除(只有阿里雲供應商)
- `FallbackLLMProvider` 包裝層:主要失敗(timeout/http_error/parse_error)
  自動切換備援;兩者都失敗才退回規則式
- Eval 分組:DeepSeek(開源)vs GPT-4o-mini(封閉源)
- `LLM_TIMEOUT_SECONDS=12`

### Google Calendar
- 開關:`GOOGLE_CALENDAR_AVAILABILITY_ENABLED`
- 憑證:`secrets/service-account.json`(gitignored)
- Calendar ID:`.env` 的 `ZHEN123_CALENDAR_ID`
- 10 秒 timeout,錯誤包成 `GoogleCalendarError` 並優雅降級(照常報價 + 通知 owner)
- 關鍵字比對用 config 值 `"枕"`(不是 `"枕123"`)

### 假日行事曆(2026-09 新增)
- 來源:公開 CDN 的政府辦公日曆表,一年一檔 JSON
- 快取:`holiday_calendar_cache` 表(一年一列,存原始 payload,**不分租戶**)
- **部署環境必須能對外連 HTTPS 到該 CDN**;連不到時不會報錯價,受影響的行程
  一律轉人工
- 開機時背景預熱今年與明年;抓失敗的年度 5 分鐘退避一次再重試
- 租戶 config 的 `special_dates` 只是覆寫層,**不代表任何年份已被涵蓋**

### 本機開發指令
- `villa` alias:切到專案目錄並啟用 venv
- `uvicorn app.main:app --reload --port 8000`
- `ngrok http 8000` + 每次都要更新 LINE Developers Console 的 webhook URL
- `ptq` alias:pytest
- 開機:專案目錄下 `$env:PYTHONPATH="."` 然後 `python scripts\set_mode.py on`
  (PowerShell);關機用 `off`
- `.env` 存 LINE channel 憑證、`OPENROUTER_API_KEY`、LLM 設定、
  `ZHEN123_CALENDAR_ID`;gitignored

---

## 八、待辦清單

### 明確待辦
1. **問題 1.5:複數意圖處理** —— 唯一還沒動的設計題,見第五章。動之前要先想清楚
   那五個設計問題,不要因為地基已經打好就倉促實作。

### 低優先 / 非阻塞
2. **owner 推播加入客人對話的 deep-link** —— 媽媽提出的需求,讓她可以直接點進
   對話接手。需要調查 LINE deep-link 可行性(例如 `line://ti/...` 搭配客人的
   LINE user ID)。
3. **Seed 腳本路徑清理** —— `seed_sandbox.py`、`add_owner.py`、`set_mode.py`
   都寫死相對路徑 `data/homestay.db`,在容器內執行會寫錯位置。
4. **補班日定價** —— 行事曆已經算得出補班日,但目前不影響價格(對要出來住一晚的
   客人來說,補班的週六還是週六)。零真實案例,依 rule-of-three 不預先做。
5. **`docs/architecture.md` 以外的文件健檢** —— 2026-09-28 已重寫 architecture,
   但 `eval_002_room_pricing_design.md`、`spec_eval002_room_pricing.md`
   等歷史設計文件仍描述舊的定價公式(它們是當時的存檔,未必需要改,但要知道)。
6. **X/Twitter thread 題材**:「測試全綠但正式環境爆掉」(stale-state bug)。

### 已完成,從清單移除
- ~~問題 1:23:00 排程開機打斷人工對話~~ —— `27bc622` + `92ce3c2`
- ~~Off 期間最後一則訊息永遠沒人回~~ —— 改用漏接彙整推播解決
- ~~`logging.basicConfig` 設定清理~~ —— 已做
- ~~兩個上線後問題的合併案例研究~~ —— `docs/case_study_intent_and_handoff.md`

---

## 九、給接手者的重點提醒

1. **先讀 `CLAUDE.md` 與 `docs/deployment.md`**,那裡有專案護城河原則與部署教訓。
   架構現況看 `docs/architecture.md`(2026-09-28 對照程式碼重寫過)。
2. **護城河四項不可碰**:pricing、state machine、availability、客人可見文字。
3. **先講清楚再動手**,Spencer 要的是理解,不是速度。
4. **有多個方向就列出來讓他選**,不要自己拍板。
5. **commit 只在 feature branch 上;不碰 main、不 push,除非 Spencer 明確指示。**
6. **發現規格與現實衝突就停下來回報。**
7. **護城河相關的改動落地後,找第二個 agent 獨立 review**(目前是 Codex,
   由 `codex-review` skill 與 PostToolUse hook 自動跑)。這不是可有可無的:
   2026-08 那次抓到 6 個,2026-09 那次抓到 8 個,其中好幾個是實作者跳不出
   自己推理鏈的那類 —— 包括「你加的這個機制讓某個本來正確的 case 變壞了」。
8. **不要為推理出來、但真實資料裡從沒出現過的問題建機制**(rule of three)。
   2026-09 那次就因為這樣加了一層語意判斷,兩輪 review 後整個拿掉。
9. **最危險的 bug 不是會報錯的那種,是「缺漏資料被當成有效資料」**:
   查不到假日就當平常日、沒寫年份就當今年。不報錯、不留 log、回覆看起來完全
   正常,只有金額是錯的。改 pricing 或 parsing 時,要問「拿到空值會怎樣」,
   不只是「拿到錯值會怎樣」。
7. 目前 907 個測試全綠,是任何改動的基準線。