# Case Study:兩起上線後真實回報的「系統該不該在此刻講話」問題

> 這份文件記錄兩起 villa_messenger 上線後(枕123民宿,約 400+ 真實客人)由民宿主人
> 親口回報的問題,以及對應的根因分析與修法。兩者的共通點是:**系統沒有壞掉、判斷
> 邏輯本身沒有錯,但在「此刻該不該由系統開口」這件事上判斷得不夠聰明**,結果從
> 「幫忙」變成「幫倒忙」。

最後更新:2026-07-29

---

## 案例一:23:00 排程開機打斷進行中的人工對話

### 現象

民宿主人(媽媽)白天/晚上正在跟某位客人一來一往手動聊天,一到 23:00,系統自動
排程開機接手,突然插進來自動回覆同一位客人。媽媽和客人雙方都覺得莫名其妙。

### 根因

系統決定「要不要自動回覆」只用一個訊號:**現在幾點**(tenant 層級的
On/Off 排程)。它完全不知道「這位客人現在正被真人處理中」——排程只分「開機時段」
跟「關機時段」,沒有 per-conversation 的狀態。

### 設計決策

三層修復,合併解決兩個問題(問題本身 + session 中額外發現的「off 時段靜默漏接」
邊界情況):

- **Layer 1 — per-customer 人工接管暫停。** 主人在 LINE 打
  `/<客人顯示名稱>`(例如 `/Wendy`)即可切換該位客人的自動回覆暫停/恢復,獨立於
  tenant 層級的 On/Off。新表 `conversation_manual_holds`,
  `app/domain/conversation_handoff_resolver.py` +
  `app/services/conversation_handoff_service.py`。同名客人有 2 位以上近期發過
  訊息時,回覆候選清單請主人指定,不做無根據的猜測。
- **Layer 2 — 舊資料重新確認閘門。** 若某對話的欄位是在 off/暫停期間累積的,
  系統開機後超過 20 分鐘才回覆的話,先送出一次性的軟性提醒
  (`RECONFIRM_STALE_CONTEXT_MESSAGE`),避免直接用可能過時的資訊報價;
  20 分鐘內視為自然接續,直接放行。`conversation_states` 新增
  `accumulated_while_off` / `last_off_mode_update_at`。
- **Layer 3 — 關機期間漏接彙整。** 過去 `messages.handled` 欄位定義了但從未真的
  被設值,等於「關機 = 沒人知道客人說了什麼」。現在該欄位確實寫入,新增
  `/待回覆` 指令供主人隨時查詢未處理訊息,並加上每 5 分鐘檢查一次、每個
  tenant-local day 最多發一次的彙整推播(`run_nightly_digest_check`,
  `app/main.py` lifespan)——這是這個原本全反應式(reactive-only)架構第一個
  主動式(proactive)背景元件。

### 實作中的意外發現

`app/adapters/line_adapter.py` 原本寫死 `customer_display_name=None`——系統從
未真正抓過 LINE 顯示名稱,儘管樣板早已有「forward-compat」的名字欄位。這直接
擊破了 Layer 1「用顯示名稱暫停」的設計前提,臨時追加了 LINE Profile API 串接
(`app.clients.line_send_client.get_profile`,best-effort 呼叫)。這個變更超出
原計畫範圍,session 中途發現後先跟 Spencer 確認才繼續。副作用:owner 推播首次
出現真實客人姓名。

### 邊界 bug 的後續修正

第一版上線後(2026-07-27),Spencer 本機測試發現:Layer 1 的暫停到期時間用
`compute_next_schedule_boundary`(找「下一個」排程邊界,不分開/關)計算,導致
白天(關機時段)按下的暫停,會在 23:00——也就是機器人真正開始插話的那一刻——
恰好到期。「有打等於沒打」。修法是新增專用的
`compute_next_active_window_end()`(`app/domain/operation_mode_resolver.py`),
永遠算到「下一次開機時段結束」為止,讓暫停能撐滿整個接下來的開機時段。舊函式
`compute_next_schedule_boundary` 維持不變,因為 `/開機` `/關機` 手動切換本身就是
想要「到下一個邊界為止」的語意。

### 驗證狀態

956 tests green。本機 uvicorn + ngrok 測試通過(2026-07-29)。Layer 2 的舊資料
重新確認提示尚未在真實線上環境人工驗證過。

### 相關 commit

- `27bc622` — 三層修復 + 邊界 bug 修正(2026-07-27 實作,2026-07-29 邊界修正)

---

## 案例二:FAQ 關鍵字劫持空房詢問的意圖(包棟案例)

### 現象

客人問:「您好,請問8/15是否還可以包棟嗎?人數9位,謝謝」。系統回了「包棟」的
FAQ 名詞解釋(「枕123是一次只接待一組客人的包棟民宿…」),完全沒回答 8/15
有沒有空房。媽媽只好手動補回「不好意思8/15滿房了」。

### 根因:兩個劫持點,而且是「反方向」的問題

訊息同時含日期(8/15)+ 人數(9位)+ FAQ 關鍵字(包棟)。「包棟」在這句話裡是
**動詞**(= 想訂整棟),不是在問「包棟是什麼」,但系統把它當成一般 FAQ 名詞
處理。

值得記錄的是:專案先前(commit `9b8e9b7`)已經修過一次類似的意圖分類問題,但
方向剛好相反——那次修的是 `NON_PRICEABLE` 機制,讓「早餐/寵物/wifi/停車…」這類
**非產品本身**的 FAQ 主題,在客人同時問價格時(例如「早餐多少錢嗎」)不被
「多少錢」搶走,能正確回答早餐政策而非硬報價。而「包棟」問題是相反方向:
**「包棟」這個 FAQ 關鍵字本身就是產品/訂房行為的名字**,當客人帶著日期或人數
問「可以包棟嗎」時,應該是 booking 訊號贏過 FAQ 名詞解釋,而不是反過來。舊的
`NON_PRICEABLE` guard 完全沒有涵蓋這個情境,所以看起來「應該修過的問題」其實
從未被修過——這也是為什麼「先重新檢視現有 guard 邏輯到底怎麼判斷的,不要假設
它不存在」在事後看是正確的提醒。

具體劫持發生在兩個獨立的判斷點,任一個沒堵住都會漏:

1. **`app/domain/inquiry_intent.py`(規則式 intent classifier)**——命中 FAQ
   關鍵字就直接回傳 `inquiry_type="faq"`,不會再往下看日期/人數。
2. **`app/services/conversation_reply_composer.py` 的 gate3**——即使 intent
   classifier 判對了,composer 組回覆文字前還有第二層 FAQ 直答閘門,同樣可能
   在這裡被 FAQ 關鍵字攔截。

### 設計決策

- **`is_booking_equivalent_topic()`(`app/domain/faq_matcher.py`)**——新增
  `_BOOKING_EQUIVALENT_TOPICS` 分類,把「描述可預訂產品本身」的主題(目前只有
  `whole_house` / 包棟)跟「描述附帶政策」的主題(早餐、寵物、wifi…)分開定義。
  這個分類集中定義一處,intent classifier 跟 composer 兩邊共用,避免兩層 guard
  各自認知不同步。
- **規則層 fallback**——`inquiry_intent.py` 在命中 FAQ 關鍵字後,若同時偵測到
  `_has_booking_signal`(有日期或人數)**且**該主題屬於 booking-equivalent,
  直接改判 `inquiry_type="availability"`,不進 FAQ 分支。
- **LLM collision judgment(第三種 LLM 觸發情境,護城河內)**——新增
  `TYPE_3_FAQ_BOOKING_COLLISION` 觸發條件
  (`app/domain/llm_fallback.py::_has_faq_booking_collision`):文字同時命中 FAQ
  關鍵字、且有日期或人數訊號時,才呼叫 LLM 做語意判斷(是在問「這是什麼」還是
  「我要訂這個」),回傳的仍只是結構化的 booking/faq 判斷結果,由規則層決定要
  不要採用——**LLM 沒有直接產生任何客人看得到的文字**,符合護城河原則。規則層
  的 `_has_booking_signal` 判斷是不呼叫 LLM 時的保底防線。
- **Composer gate3 同步收斂**——`_should_answer_gate3_faq` /
  `_is_booking_equivalent_quote` 用同一個 `is_booking_equivalent_topic()`,確保
  第二層閘門跟 intent classifier 的判斷邏輯不會互相矛盾。

### 附帶修的:單晚推定滿房查詢

原案例還有第二個小缺口:客人只給了單一日期(8/15),沒給退房日或住幾晚,原本
`missing_fields` 會卡住,系統無法對 Google Calendar 做空房檢查,只能被動等客人
補資訊。新增 `app/domain/availability_probe.py::with_single_night_availability_probe`
——在沒有明確住宿天數時,推定「單晚」去試探空房 API,但這個推定值只用於
calendar 探測,**不會**寫入真正的 `checkout_date`、`missing_fields` 或進入報價
計算。滿房情境下可以直接回覆客滿,文案也做了自然化調整,不會讓客人覺得系統在
用一個「我沒說過的日期」回答。

### 驗證狀態

672+ tests green(該 commit 新增/調整約 20 個檔案的測試)。

### 相關 commit

- `53113fd` — 兩層 FAQ 劫持修正 + 單晚推定滿房查詢(2026-07-27)

---

## 兩案例的共同教訓

1. **「回答得到」不等於「回答得對」。** 兩起案例中,系統都成功辨識出訊息(排程
   邏輯本身沒錯、FAQ 關鍵字比對本身也沒錯),問題出在「這個回答/這次接手,是不
   是此刻該做的事」——這類 timing / priority 判斷,比單純的正確性 bug 更容易被
   忽略,因為每個模組單獨測試時都是對的。
2. **「之前修過類似問題」不代表這次也涵蓋到。** 案例二裡,`NON_PRICEABLE` guard
   解決的是「FAQ 該不該被 booking 訊號打斷」,這次的問題方向相反——「booking
   訊號該不該被 FAQ 關鍵字打斷」。表面關鍵字相同(都跟「FAQ vs 訂房意圖」有關),
   實際是兩個不同方向的 guard,必須各自明確定義、各自測試。
3. **多層防線要共用同一份分類定義,不能各自判斷。** 案例二的 intent classifier
   和 composer gate3 是兩個獨立的程式碼路徑,若各自寫一份「這是不是 booking
   主題」的判斷邏輯,遲早會出現兩邊不同步、其中一層漏接的情況。把分類邏輯
   (`is_booking_equivalent_topic`)集中定義一處、多處呼叫,是這次修法刻意的
   設計選擇。
4. **護城河邊界在壓力下仍然守住。** 案例二引入了第三種 LLM 觸發情境
   (`TYPE_3_FAQ_BOOKING_COLLISION`),但 LLM 輸出仍然只是「是不是 booking 意圖」
   的布林/意圖判斷,由規則層決定要不要採用,客人看到的文字仍然全部來自
   `reply_templates.py`。即使新增使用情境,也沒有讓 LLM 越界去碰報價或回覆文字。

## 尚未完成的部分

- 案例一 Layer 2(舊資料重新確認提示)尚未在真實線上環境人工驗證過。
- Owner 推播加「跳轉到該客人對話」的 LINE deep link 仍是待辦(見 CLAUDE.md)。
- 案例二的 `checkout`(退房)主題仍刻意排除在 `NON_PRICEABLE` 之外——關鍵字
  「退房」跟報價訊息裡的退房日期欄位（例如「5/14 退房 多少錢」）會互相碰撞,
  安全納入需要先讓日期解析跑在 FAQ 比對之前,是獨立的後續項目(見
  `app/domain/faq_matcher.py` 內註解與記憶
  `project_faq_wins_over_price_intent`)。
