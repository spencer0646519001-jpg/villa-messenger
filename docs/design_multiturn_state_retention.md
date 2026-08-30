# 設計提案:多輪對話狀態保留缺口

> 狀態:**3-A、3-B 已定案並實作完成;3-C 經 Spencer 決定跳過**(pet_type/月齡
> 細節不值得追,has_pet 已經夠用)。這是護城河核心(狀態機)的改動,依
> CLAUDE.md 工作流程,先設計討論、Spencer 拍板,才動手實作。
> 起源:eval v1.1 跑分 22/50 → 30/50 的過程中,發現剩下的失敗案例裡有 6 個
> (candidate_40、candidate_41、failure_403、failure_404、failure_326、control_11)
> 表面上分屬不同 cluster,但深入追查後,根因其實只有三種,而且都在同一層:
> `ConversationStateService`(STAGE B,狀態合併)。
> 最後更新:2026-08-30(3-B 實作定案 + 完成)。

---

## 0. 為什麼不能用關鍵字/分類器修

在 eval 修復過程中,曾經誤以為 candidate_40 的問題是「意圖分類沒把這句話認成
`booking_question`」,加了一條「`訂` + 完整日期範圍 → `booking_question`」的規則。
結果這條規則讓 candidate_40 的 `inquiry_type` 從正確的 `unknown` 變成錯誤的
`booking_question`,違反 gold 的預期——**問題根本不在意圖分類,而在「這句話的
日期有沒有被存進 conversation_state」**。已撤銷該規則(commit `ac8f084`)。

這是一個重要的教訓:多輪保留問題的正確修法在 `ConversationStateService` /
`ConversationReplyComposer` 這一層,不是在 `inquiry_intent.py`。本提案就是照這個
教訓,直接對準狀態合併層設計。

---

## 1. 現況機制(pre-flight,寫方案的基礎)

`ConversationStateService.record()`(`app/services/conversation_state_service.py`)
用「兩層政策」決定要不要把這一輪解析出來的欄位存進 `conversation_states`:

- **OPEN(新開一筆狀態)**:只有當這則訊息本身 `parsed_as_inquiry` 且
  `intent in _QUOTE_RELEVANT_INTENTS`(`price`/`availability`/`booking_question`)
  才會新開。純聊天(「嗨」)不會誤開狀態。
- **UPDATE(合併進既有狀態)**:只要有一筆狀態已經是 `in_progress`,**不管這則訊息
  自己的意圖分類是什麼**,只要解析出任何 slot(`_SLOT_KEYS`:日期/人數/房數/寵物/
  BBQ)就合併進去(`_update_active`)。這是文件註解裡講的「goldfish-memory fix」,
  刻意設計成比意圖分類寬鬆。
- 合併方式是 **COALESCE**:新值非 None 就覆蓋,新值是 None 就保留舊值
  (`_merge_row`)。沒有「這是全新一次詢問,舊資料要清空」的概念。

另外,`app/api/line_webhook_routes.py` 裡有一個獨立的「離題判斷」機制
(`_looks_off_topic_against_open_state` + `judge_state_continuation`,LLM 版),
但那是處理**完全没带 slot 的純聊天打斷開放中對話**的情境(比如訂房訂到一半突然問
天氣),跟本提案要解的問題不是同一件事——下面三個案例的訊息都**帶有真正的 slot
資料**,不會被那個機制攔到。也順便發現:`eval/replay.py` 完全没有跑這段離題判斷
邏輯,跟正式 webhook 路徑不完全一致,但因為跟本提案無關,先記錄不深究。

---

## 2. 三個獨立根因

### 根因 A:OPEN 條件太嚴,帶 slot 但意圖不明的訊息連存都存不進去

**受影響:candidate_40(連帶 candidate_41)**

`candidate_40` 輸入「是的\n訂8/2～8/4兩晚的」,history 裡兩輪都不構成
quote-relevant(「現在還來得及嗎」「好的!」)。到這一輪,日期本身**解析完全正確**
(`parse_stay_dates` 給出 checkin=8/2、checkout=8/4),但因為 gold 認定這句話的
`inquiry_type` 該留 `unknown`(不是明確的訂房問句),`_open_if_inquiry` 的兩個條件
都不滿足 → 沒有任何一筆 `in_progress` 狀態存在 → 這輪解析出來的日期**直接被丟棄,
沒有地方可以合併**。`candidate_41`(下一輪只給聯絡資訊)因為前面沒開成狀態,自然也
接不到日期。

### 根因 B:COALESCE 合併沒有「全新詢問應該重置舊欄位」的概念

**受影響:control_11**

`control_11` 的 history 是舊詢問(7/25 的空房問題 + 「7/26退房\n8大2小」),已經開了
一筆狀態,累積了 `checkin=7/25`(單一日期預設當入住)、`checkout=7/26`、
`adult_count=8`、`child_count=2`。**當輪**輸入「你好請問7/11-12 10人有房嗎」是一句
**完整獨立、自帶全新日期+人數**的詢問(gold 註記:「全新詢問(不同日期),忽略先前
不相關雜訊」)。但 `_update_active` 的 COALESCE 邏輯只會覆蓋這輪**有解析出新值**的
欄位(checkin/checkout/adult_count 被新值蓋掉),`child_count` 這輪沒提到 → 舊的
`child_count=2` 原封不動留著 → `guest_count` 變成 10+2=12(錯的)。

### 根因 C:`pet_type` / `needs_pet_count_confirmation` 從來沒有被追蹤進 `conversation_states`

**受影響:failure_403、failure_404**

`_SLOT_KEYS`、`conversation_states` schema、`log_payload_to_state_slots` 三處都只
追蹤 `has_pet`/`pet_count`,完全沒有 `pet_type`/`needs_pet_count_confirmation` 這兩
個欄位——它們只存在於逐則訊息的 `inquiries` 表(`schema.sql:136`),從結構上就不可能
跨輪存活,跟 OPEN/UPDATE 邏輯對不對都無關。

已確認(`grep` 全專案):這兩個欄位**目前沒有驅動任何客人看得到的回覆文字**,只影響
`inquiries` 稽核紀錄跟這次 eval 的欄位比對。風險最低、也最不急。

*(`failure_326` 高度疑似也是根因 A 的變形——來源 history 是 7 行無標籤表單回覆
(含 BBQ + 車位兩行),比 44e3fc0 修過的 5 行版本多兩行,可能沒被
`looks_like_structured_form_reply` 認出來,導致那一輪本身沒能 OPEN 狀態。這點還沒
實際追蹤驗證,列為根因 A 修完後的第一個驗證項目,不在本提案的設計範圍內另外處理。)*

---

## 3. 提案設計

### 3-A. 放寬 OPEN 條件

在 `_open_if_inquiry` 加一個「有沒有足夠強的 slot 證據」的旁路條件,跟現有 UPDATE
的哲學一致(UPDATE 早就不看意圖分類,只看有沒有 slot)。

```python
def _open_if_inquiry(self, message, decision, slots, off_kwargs):
    intent = decision.log_payload.get("inquiry_intent")
    is_quote_relevant = decision.parsed_as_inquiry and intent in _QUOTE_RELEVANT_INTENTS
    if not (is_quote_relevant or self._has_strong_slot_evidence(slots)):
        return None
    ...
```

**需要 Spencer 決定的地方:`_has_strong_slot_evidence` 要多寬鬆?**

- **寬鬆版**:比照 UPDATE,只要 `_SLOT_KEYS` 任一個非 None 就算(跟現有
  `_has_slot` 共用同一份邏輯)。好處是一致、簡單;風險是像「開2房」這種單獨出現
  的訊息,脫離上下文時也會誤開一筆狀態(不過這種狀態如果後面沒人接話,會自然
  過期,不會真的送出錯的報價)。
- **保守版**:只在「同時有 checkin_date 跟 checkout_date」(完整日期範圍)時才開,
  單一個房數/人數之類的弱訊號不觸發。剛好精準覆蓋 candidate_40 這個案例,不擴大
  其他情境的行為。

個人傾向保守版,理由是:目前唯一有真實證據(gold 案例)支持要放寬的情境就是「完整
日期範圍」,寬鬆版是在沒有案例驗證的情況下擴大護城河的行為面,風險/收益不對等。

### 3-B. 一組新的「完整日期+人數」換一塊全新白板(已實作,非原提案的原地清空)

**設計在提案討論階段整個換了方向**——原本打算在 `_update_active` 原地清空舊欄位,
後來 pre-flight 發現這條路要碰 repository 的 COALESCE SQL、要讓 `update_slots`
多一種「明確清空」的語意,風險偏高。改成利用 repository 已經有的
`create()` / `mark_expired()`:**把舊狀態標記過期、直接開一塊全新的白板**,新白板
只帶這一輪自己解析出的欄位。「一塊狀態的生命週期內只增不減」這個不變量完全沒被
打破,只是多了「一塊狀態何時結束、換下一塊」的邏輯。

觸發條件(`_is_fresh_full_date_range`,兩個條件同時成立才換):

1. 這輪**同時**給出 `checkin_date` + `checkout_date`,且跟白板上現存的**不一樣**。
2. 這輪給出的 `adult_count`,跟白板上現存的 `adult_count` **不一樣**。

Spencer 討論時抓到兩個原提案沒想到的真實情境,都已經反映進最終設計:

- **只改日期、沒重講人數**(「等一下,另一組日期7/11-12可以嗎」)→ 條件 2 不成立
  (這輪 `adult_count` 是 `None`)→ **不換白板**,原地更新日期,房數/寵物照舊保留。
- **順口把人數也重講一次、但是同一群人**(「另一組日期7/11-12 **10人**可以嗎」,
  白板上本來就是 10 人)→ 條件 2 不成立(10==10)→ **不換白板**。
- 原本設計用「人數加總」(大人+小孩)比對,寫測試時才發現一個真實的碰撞:
  control_11 舊狀態是「8大2小」(加總10),新訊息「10人」被解析成
  `adult_count=10, child_count=None`(加總也是10)——**加總比對會判斷成同一群人,
  完全漏掉 control_11 這個目標案例本身**。改成直接比 `adult_count`(而非加總),
  因為 `guest_count_parser` 對「N人」這種裸人數的既定慣例就是解成 `adult_count`,
  是比較穩定的錨點;只有 `child_count` 變動、`adult_count` 沒變,視為對同一筆訂房
  的修正,不換白板。
- 特意**不要求**這輪訊息自己的意圖分類是 quote-relevant——實測「10人 另一組日期
  7/11-12」「改一下 7/11-12入住 10人」這類真實講法,意圖分類常常是 `unknown`
  (沒有訂房關鍵字),硬性要求會讓這個機制在真實情境幾乎不會觸發。改成跟
  `_should_open` 的日期範圍旁路同一套哲學:兩個獨立欄位(完整日期+人數)同時改變,
  本身就是夠強的結構證據。

判斷錯的代價分析(說服自己這個門檻夠安全):錯誤地換了白板,最壞結果是重問一次
房數/寵物(客人多回一句話);錯誤地沒換,最壞結果是保留了不相關的房數偏好。**兩種
錯法都不會算出錯誤報價**,這是可以接受這個門檻的關鍵。

舊白板標記成 `expired`(不是 `completed`——`completed` 原意是「已經報價完成」,標
成這個會讓稽核時誤以為真的報價過)。

### 3-C. 補上 `pet_type` / `needs_pet_count_confirmation` 的多輪追蹤

- `schema.sql`:`conversation_states` 加兩欄
  `pet_type TEXT`、`needs_pet_count_confirmation INTEGER NOT NULL DEFAULT 0`。
- `_SLOT_KEYS` 加 `pet_type`(`needs_pet_count_confirmation` 是衍生欄位,不需要
  是獨立可合併的 slot,可以在讀取時用 `has_pet and pet_count is None` 現算,不一定
  要存欄位——待確認是否有其他地方需要直接讀存好的值)。
- `log_payload_to_state_slots` 加對應映射。
- 既有 DB 需要手動 `ALTER TABLE`(比照專案裡 `wants_bbq` 上線時的模式)。

純加欄位、不改變任何現有欄位的合併語意,是三者中風險最低、最適合先做的一個
(雖然如前述,目前沒有客人看得到的行為會因此改變,急迫性也最低)。

---

## 4. 建議順序與驗證方式

**最終結果(依序完成):**

1. **3-C**:Spencer 決定跳過。pet_type/月齡細節不值得追蹤,has_pet 已經夠回答
   「該不該問寵物費」這個實際問題。
2. **3-A**(保守版,只認完整日期範圍,不吃寬鬆版的任意單一 slot):實作
   commit `3409642`,經過 6 輪 Codex review 陸續補上邊界案例(FAQ/urgent 誤觸發、
   LLM 明確拒絕的兩種觸發路徑、意圖降級後 `missing_fields` 沒重算),最後一輪乾淨
   過關。candidate_40/41 通過。failure_326 沒有跟著修好,驗證後確認是
   `looks_like_structured_form_reply` 認不出 7 行版的無標籤表單回覆(見根因 A 後的
   附註),留待日後另外處理。
3. **3-B**:採用「換一塊新白板」而非原提案的「原地清空」(理由見 3-B 節)。
   Spencer 討論時抓到「順口重講同樣人數」跟「只改日期沒重講人數」兩個真實情境,
   都已經反映進最終的觸發條件。control_11 通過。

每一步都各自 commit、各自跑一次 `python -m eval.runner` 確認案例真的轉為通過,
commit 後都跑過 Codex 獨立 review、依回饋修正到過關為止,沒有合併成一次大改動。
