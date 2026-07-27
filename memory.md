# villa_messenger — 專案記憶檔

> 用途:給 Claude Code(或任何新的協作對象)快速理解這個專案的來龍去脈。
> 最後更新:2026-07-27
>
> **閱讀順序建議**:先讀「零、專案是什麼」與「一、護城河原則」,再看
> 「五、目前正在處理的問題」。中間的歷史章節可以之後再看。

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
4. **不要主動 commit** —— Spencer 會自行測試(含真機測試)後 commit。
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
- **問題 1(進行中)**:23:00 排程開機打斷人工對話 —— 交接給 Claude Code

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

## 五、目前正在處理的問題

### 問題 1:23:00 排程開機打斷進行中的人工對話 ⭐ 最高優先

#### 真實案例

民宿主人(媽媽)白天/晚上正在跟某位客人手動聊天(一來一往),時間一到 23:00,
系統自動開機接手,突然插進來自動回覆同一位客人。媽媽和客人雙方都覺得莫名其妙。

#### 核心缺口

系統目前只用「現在幾點」決定要不要自動接手,**完全沒有「這個客人是不是正在跟
真人對話中」的判斷**。

#### ✅ 調查結果(Codex 已完成唯讀調查,結論可信)

**(1) 23:00 不會主動推送任何訊息**

專案裡沒有定時任務在 23:00 掃描既有對話,也沒有主動 push 客人的程式。
23:00 的作用只是:**下一則客人訊息進來時**,系統依當下時間計算模式為 On,
才使用該則訊息的 `replyToken` 回覆。

實際流程:客人送訊息 → webhook 收到 → `InquiryService` 當下計算 On/Off →
On 才可能回覆;Off 只儲存 → 沒有客人新訊息就不會有任何動作。

相關檔案:`operation_mode_resolver.py`、`operation_mode_service.py`、
`line_webhook_routes.py`

**所以真實案例更精確的描述是**:媽媽正在人工聊天,23:00 後客人又傳來一則訊息,
系統因為此時已是 On,立刻插入自動回覆。**不是系統在 23:00 無中生有地主動發話。**

**(2) 系統看不到媽媽從 OA Manager 發出的人工回覆** ⭐ 關鍵前提

LINE Messaging API 沒有「官方帳號操作人員從 OA Manager 回覆客人」的對應 webhook
事件。本專案的 `line_adapter.py` 也只接受 `type == "message"` +
`message.type == "text"` + `source.type == "user"`。

**結論:無法直接知道媽媽是否回覆過某位客人。**
(媽媽用自己的 LINE 傳 `/開機` 等指令是另一個獨立對話,不能代表她回覆了誰。)

**(3) 關機期間的客人訊息確實有保存**

Off 模式仍會:規則式解析、寫入 `messages`、訂房詢問也寫入 `inquiries`、
建立或更新 `conversation_states`。只是不回客人、不推播媽媽(緊急訊息除外)。

`messages` 可用欄位:`tenant_id`、`platform`、`platform_user_id`、
`system_state_at_time`、`created_at`(伺服器寫入的 **UTC** 時間)。

**(4) 不建議把人工接管欄位掛在現有 `conversation_states`**

現有 state 是「訂房資料累積器」,不是一般聊天 session:只有訂房相關意圖才會建立、
預設 24 小時到期、FAQ 對話不一定有 state。掛在這裡會讓兩個概念混在一起。

#### 設計方向(Codex 提案 + Claude 修正,**尚未定案,等 Spencer 確認**)

**採用方案 A:從 `messages` 推導「人工接管冷卻期」**

不增加新資料表。每次排程 On 時收到客人訊息,查這位客人的近期訊息:
1. 若近期已有 suppressed 紀錄 → 繼續靜默並延長冷卻期
2. 否則檢查偵測窗內是否有足夠的 Off 模式來訊
3. 命中時,本則訊息仍儲存,但不自動回覆
4. 每一則被靜默的來訊都重新延長冷卻期(滑動冷卻)
5. 客人安靜超過冷卻期後,下一則訊息恢復正常自動處理

**已同意的兩項保護:**

- **只攔截「排程自動 On」**:若媽媽主動輸入 `/開機`,視為她明確要求系統接手,
  直接恢復自動處理。模式結果要從 On/Off 細分為 manual on / manual off /
  schedule on / schedule off,只有 `schedule on` 才檢查人工接管。
- **接管成立時結束舊的訂房 state**:關機期間的人工聊天也會累積訂房 state。
  若只靜默 60 分鐘但保留 state,冷卻期結束後客人傳一句「謝謝」,系統可能用一小時
  前的日期人數突然報價。因此第一次判定接管時,將該客人現有 `in_progress` state
  標為 expired,冷卻期間只儲存訊息不更新 state。

#### ⚠️ Claude 對 Codex 參數建議的三點修正(重要,**尚未取得 Spencer 確認**)

**修正 1:門檻不能設 1**

Codex 建議 `minimum_off_mode_messages: 1`。這不行 ——「Off 期間有一則來訊」
**對所有客人都成立**,包含那些留言後沒人理、正等 23:00 系統回覆的客人。
門檻設 1 等於把 23:00 開機功能整個關掉。

**修正 2:光看「則數」不夠,必須看「間隔」**

客人常把一句話拆成多則泡泡送出(「你好」「請問8/15有空房嗎」「9個人」,10 秒內)。
這是一個人在打字,不代表媽媽回過話。

**真正能區分的訊號是:客人在沒得到系統回覆的情況下,隔了一段時間又開口**
—— 那段沉默就是媽媽在打字的時間。

因此規則應該是:偵測窗內有 **≥2 個「輪次」**,且輪次之間間隔 ≥ 2 分鐘。
連發訊息要先做 burst 合併(間隔 < 30 秒視為同一輪)。

**修正 3:建議的參數**

```json
"human_handoff": {
  "enabled": true,
  "scheduled_on_only": true,
  "detection_window_minutes": 45,
  "minimum_customer_turns": 2,
  "turn_gap_seconds": 120,
  "burst_merge_seconds": 30,
  "cooldown_minutes": 60
}
```

- `detection_window_minutes` 45(不是 30):容納聊得慢的對話
- `burst_merge_seconds` 30(不是 60):真人回一句話通常不會快過 30 秒

#### ⚠️「通知媽媽」是必要功能,不是可選項

Codex 原本的方案是「命中接管 → 完全靜默,也不推播媽媽」。**這不安全。**

因為有一個無法從時間節奏區分的情境:**客人在催**。

```
22:59  客人:8/18 可以包棟嗎 8人   (沒人理)
23:05  客人:請問有人在嗎?          (還是沒人理)
       ↑ 兩次開口、間隔 6 分鐘 → 系統判定「有人在陪」→ 靜默
```

但根本沒人陪他。這是純粹的誤判,而且**從訊息時間上完全分不出來**
(催促的節奏跟真的在對話一模一樣)。

**解法:對客人靜默,但推播通知媽媽。**
- 媽媽真的在聊 → 收到一則她已經知道的通知,輕微雜訊
- 媽媽其實沒在處理(誤判)→ 她被提醒了,可以自己接手

代價很小,但把「訊息掉進黑洞」的情境消掉了。
(實作時需確認 On 模式下 owner 推播的既有行為,不要重複推播。)

#### 三個待 Codex/Claude Code 確認的實作細節

1. **緊急訊息路徑**:Off 模式現有的緊急訊息例外(會推播媽媽)必須維持,
   不能被人工接管的靜默一併吃掉。
2. **時區**:`created_at` 是 UTC,營業模式用台北時間計算。
   偵測窗的時間比較全部統一在 UTC 做,不要混。
3. **測試基準**:上次完整測試套件因工具 120 秒上限中斷,沒拿到基準。
   動 code 前請先跑出一次完整綠燈(必要時分批跑)。

#### 四種情境的預期行為(已推演)

| 情境 | 系統應該怎麼做 | 狀態 |
|---|---|---|
| 媽媽正在聊,23:00 後客人再開口 | 閉嘴 + 通知媽媽 | ✅ 設計可涵蓋 |
| 22:59 傳一句,之後沒再傳 | 永遠不回(**既有問題**,見下) | ⚠️ 待決定 |
| 22:59 傳一句,23:05 又催一句 | 誤判閉嘴,但有通知媽媽兜底 | ⚠️ 靠保險兜住 |
| 23:01 第一次來訊 | 正常自動回覆 | ✅ 不受影響 |

#### 預計修改範圍(Codex 估計)

- `operation_mode_resolver.py`、`operation_mode_service.py`:提供 On 的來源
- 新增 `human_handoff_service.py`:純規則判斷近期活動
- `message_repository.py`:查詢同一客人的近期 Off/suppressed 訊息
- `inquiry_service.py`、`inquiry_decision.py`:產生靜默決策與紀錄原因
- `conversation_state_repository.py`、`conversation_state_service.py`:
  接管時清除舊 state,冷卻期間不累積
- `conversation_reply_composer.py`:確保接管決策保持靜默
- `line_webhook_routes.py`:組裝服務與設定
- 租戶 `config.json`
- 對應測試

**不會修改**:pricing、availability、客人可見模板文字、LLM prompt 或 LLM 意圖判斷。
會碰到 conversation state,但只用確定性規則終止舊 state,不交給 LLM。
人工接管判斷放在 LLM 前面,命中時不呼叫 LLM。

#### 驗收標準

- 模擬媽媽手動聊天情境後,23:00 後該客人來訊,**不會收到自動回覆,但媽媽收到通知**
- **同時段新進線的其他客人正常收到自動回覆**,不受影響
- 冷卻期結束後,該客人再來訊時恢復正常自動回覆
- 既有 907 個測試維持全綠

---

### 問題 1.5:複數意圖處理(優先度低於問題 1)

> **前置條件:問題 1 完成、通過真機驗證並 commit 之後才開始。**

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

### 最高優先
1. **問題 1:23:00 排程開機打斷人工對話** —— 設計已成形,等 Spencer 確認參數後實作

### 次要
2. **問題 1.5:複數意圖處理** —— 問題 1 驗證通過後才開始
3. **「Off 期間最後一則訊息永遠沒人回」** —— 2026-07-27 推演情境時發現的**既有問題**,
   不是這次改動造成的。22:59 傳一句、之後沒再傳的客人,23:00 開機後不會被處理,
   要等他自己再開口。
   - 選項 A:不處理(維持現狀,客人自己會再問)
   - 選項 B:開機時掃描 Off 期間未回覆訊息並補一則 → 但這會變成「主動推送」,
     跟現在架構不同,而且**正好會撞到問題 1 要防的情境**
   - **建議先記著,不要跟問題 1 混在一起做**(兩者的解法會互相打架)

### 低優先 / 非阻塞
4. **owner 推播加入客人對話的 deep-link** —— 媽媽提出的需求,讓她可以直接點進
   對話接手。需要調查 LINE deep-link 可行性(例如 `line://ti/...` 搭配客人的
   LINE user ID)。
5. **Seed 腳本路徑清理**(`seed_sandbox.py`、`add_owner.py` 的寫死相對路徑)
6. **`logging.basicConfig` 設定清理**
7. **兩個上線後問題的合併案例研究**(Spencer 要求,問題 1 + 問題 2 一起寫)
8. **X/Twitter thread 題材**:「測試全綠但正式環境爆掉」(stale-state bug)

---

## 九、給接手者的重點提醒

1. **先讀 `CLAUDE.md` 與 `docs/deployment.md`**,那裡有專案護城河原則與部署教訓。
2. **護城河四項不可碰**:pricing、state machine、availability、客人可見文字。
3. **先講清楚再動手**,Spencer 要的是理解,不是速度。
4. **有多個方向就列出來讓他選**,不要自己拍板。
5. **不要主動 commit。**
6. **發現規格與現實衝突就停下來回報。**
7. 目前 907 個測試全綠,是任何改動的基準線。