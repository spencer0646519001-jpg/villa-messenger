# eval v1.1 gold 集 — 待處理清單(2026-08-24 盤查)

> 這份文件記錄一次針對 v1.1 gold 集(`villa_eval_private/eval_v1/expanded_gold_50_v1_1.jsonl`)
> tri-state 欄位(`has_pet`/`pet_count`/`wants_bbq`/`infant_count`/`child_count`)一致性的全面盤查結果。
> `eval/scoring.py` 這邊的程式碼修正已經 commit(`6afb0b8`),這份文件記的是**還沒動、需要
> 你決定要不要套用**的部分:① gold 標記本身的殘留錯誤、② 盤查過程中意外挖到的新解析器 bug。
>
> 盤查範圍:has_pet / pet_count / wants_bbq / infant_count / child_count 五個欄位全部核對過,
> **只有 wants_bbq 有標記問題(3 題),其他 4 個欄位是乾淨的**——這 4 個欄位目前的 eval 失敗
> 案例全部是真實的解析器/狀態機 bug(已知或本次新發現),不是標記錯誤。

---

## 1. wants_bbq 標記修正(3 題)

### 背景

v1→v1.1 那次修正把 46 題的 `has_pet`/`pet_count`/`wants_bbq`/`infant_count`/`child_count`
從「沒提到就預設 False」改成「沒提到就是 null」。這次盤查發現有 3 題漏掃,客人整場對話
從頭到尾沒提過烤肉,但 gold 還是標 `false`(不是 `null`)。

### 要改的 3 題

**candidate_29**(gold 檔案第 43 行)
```diff
- "wants_bbq": false,
+ "wants_bbq": null,
```
對話:「請問八月的六日一是否還有空房」/「請問如果8/23-25\n6大兩小 兩間4人房多少錢呢？」/
「請問費用呢」——全程沒提過烤肉。

**candidate_30**(gold 檔案第 44 行)
```diff
- "wants_bbq": false,
+ "wants_bbq": null,
```
對話同上 + 「我跟朋友說一下」——全程沒提過烤肉。

**candidate_31**(MULTI_TURN 分類)
```diff
- "wants_bbq": false,
+ "wants_bbq": null,
```
對話同上 + 「可以帶寵物對嗎」——全程沒提過烤肉。
(這題的 `has_pet` 已經正確標 `null`,只有 `wants_bbq` 沒改到。)

### 套用步驟

1. 在 `villa_eval_private/eval_v1/expanded_gold_50_v1_1.jsonl` 裡把上述 3 題的
   `wants_bbq` 從 `false` 改成 `null`(其餘欄位不動)。
2. 重新計算整份檔案的 SHA-256:
   ```
   python -c "import hashlib; print(hashlib.sha256(open('expanded_gold_50_v1_1.jsonl','rb').read()).hexdigest())"
   ```
3. 把新的雜湊值貼到 `villa_messenger/eval/runner.py` 的 `FROZEN_GOLD_SHA256` 常數
   (目前是 `df6e9f4570a9edacba9796787601fe84acbf50580d8b11445db0152615291d94`)。
4. 重跑 `python -m eval.runner`,確認:
   - `[eval] gold SHA-256 verified: ...` 顯示新雜湊,沒有 ABORT
   - MULTI_TURN 應該從 3/10 回到 5/10(candidate_29/30 直接過,candidate_31 仍會因為
     has_pet 那個獨立的解析器 bug——見下方第 2 節——繼續失敗,是正常的)
   - BBQ 欄位準確率應該從 77.8%(14/18)再往上跳到接近滿分(17/18,剩 failure_326
     那題是已知的 PARSER_MISS,不是標記問題)
5. 要不要順便把版本號從 v1.1 推進到 v1.1.1 或直接算 v1.2、要不要留一份
   `expanded_gold_50_v1_1_orig.jsonl` 備份——這個交給你決定,不同專案的版本慣例
   我沒有足夠上下文替你做主。

---

## 2. 新發現的解析器 bug(不是標記問題,是真的程式漏洞)

### 現象

candidate_25 / candidate_26 這兩題(同一場對話,只是結束在不同輪)在目前的 eval 裡,
`checkin_date`/`checkout_date`/`adult_count`/`guest_count`/`pet_count` 全部是 `None`,
即使客人第一句話就把日期、人數、寵物狀態都講清楚了:

```
彭璟蕙
[PHONE]
8／8-8／9
6位
無寵物
```

### 根因(已用程式直接驗證,不是猜測)

這句話單獨拿去 `parse_inquiry()` 解析,日期/人數/寵物**全部解析正確**
(`checkin_date='2026-08-08' checkout_date='2026-08-09' adult_count=6 has_pet=False`)。
問題不在欄位解析器,而在**意圖分類**這一層(`app/domain/inquiry_intent.py`):

1. 這句話是「無標籤逐行填空」格式(姓名/電話/日期/人數/寵物狀態,每行一個值,
   但沒有「聯絡人：」「入住日期：」這種標籤)。`app/domain/form_reply_detector.py`
   的 `looks_like_structured_form_reply()` 要求至少 3 行有 `標籤:內容` 這種
   冒號分隔格式(`_MIN_LABELED_LINES = 3`),這句話一行都沒有 → 判定為
   **不是**結構化表單回覆。
2. 沒被表單偵測器接住,就繼續往下走到 FAQ 比對:「無寵物」命中了 `pets` 這個
   FAQ topic(tier 1)。
3. `pets` 這個 topic 沒有被標記成 `is_booking_equivalent_topic`(回傳 `False`),
   所以即使這句話明明帶著完整的訂房日期,整句話還是被分類成純 `faq` 意圖,
   不是 `price`/`availability`/`booking_question`。
4. 因為意圖不是 quote-relevant,`ConversationStateService._open_if_inquiry()`
   的閘門不會通過,**這整輪訂房資訊就完全沒有被存進 conversation_states**——
   不是資料掉了,是狀態根本沒被建立。後面兩輪("2台汽車"、"要烤肉")也都不是
   quote-relevant,所以整場對話都沒有累積到任何欄位。

驗證用的指令(可重現):
```python
from app.domain.faq_matcher import match_all_faq_topics, is_booking_equivalent_topic
from app.domain.form_reply_detector import looks_like_structured_form_reply
from app.domain.text_normalizer import normalize_for_parsing

t = normalize_for_parsing('彭璟蕙\n[PHONE]\n8／8-8／9\n6位\n無寵物')
print(looks_like_structured_form_reply(t))          # False
print(match_all_faq_topics(t))                       # [('pets', tier=1)]
print(is_booking_equivalent_topic('pets'))            # False
```

### 影響範圍

真機上任何客人用「逐行填空但不寫標籤」的方式回覆(尤其是回答完寵物欄位的表單),
只要那句話同時命中 FAQ pets/wifi/parking 這類非 booking-equivalent 的 tier-1
topic,整輪對話的日期/人數就會被靜默漏接,不會進 `conversation_states`,系統
不會追問缺什麼,因為它根本不知道客人已經給過資訊。這比 gold 標記問題嚴重
——這是會實際影響真客人的 bug。

### 可能的修法方向(尚未設計,先記著)

- 方向 A:放寬 `looks_like_structured_form_reply()`,允許「多行、每行一個值、
  日期/人數其中至少一項可解析」的無標籤格式也算表單回覆。風險:可能把真的
  閒聊或 FAQ 問句誤判成表單回覆。
- 方向 B:在 FAQ-topic 分類分支裡,即使命中非 booking-equivalent topic,
  只要 `_has_booking_signal(text)` 為真(有日期或人數訊號),也升級成
  quote-relevant,而不是只看 `is_booking_equivalent_topic`。這個改法影響面
  更廣(所有 tier-1 FAQ topic 都會受影響),需要跟 `ConversationReplyComposer`
  裡的 FAQ 路由邏輯(`_should_answer_gate3_faq` 等)對過,避免產生新的衝突。

這兩個方向都牽涉意圖判斷/狀態機,照專案慣例要先設計、你確認過才能動手。
