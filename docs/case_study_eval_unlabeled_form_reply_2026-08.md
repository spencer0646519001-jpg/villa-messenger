# Case Study:eval 盤查資料一致性時,意外挖到一個真的會漏接客人的 bug

> 這份文件記錄 2026-08-24 的一次連鎖事件:原本只是要修 eval v1.1 gold 集裡
> `wants_bbq`/`has_pet` 兩個欄位「沒提到卻被標成 False」的 eval 計分問題,盤查
> 標記一致性的過程中卻挖到一個真的會讓真實訂房資訊被系統完全漏接的 bug——
> 而且這個 bug 是 0cba8d7(2026-08-04「定型表單解析全面修正」)當時完全沒觸及
> 的變體。

最後更新:2026-08-24

---

## 起點:eval 分數看起來偏低,但懷疑不是程式的錯

`eval/scoring.py` 讀 `wants_bbq`/`has_pet` 的方式,是直接讀持久化的
`conversation_states` 欄位——但這兩欄在 schema 是 `INTEGER NOT NULL DEFAULT 0`,
資料庫層級**沒有「不知道」這個狀態可以存**,只要客人問過任何跟訂房相關的東西,
這兩欄就會拿到預設值 `False`,不管客人有沒有講過烤肉/寵物。這先被確認是「eval
量錯地方」,不是「程式邏輯錯」——生產環境的報價/回覆文字本來就對「沒提過」跟
「明確說沒有」一視同仁,客人體驗不受影響。修法是讓 eval 自己重新解析每一輪的
原始文字重建 tri-state,不動生產程式碼(見 `eval/scoring.py` 的 `_ever_mentioned`,
commit `6afb0b8`)。

## 盤查標記一致性時,順便對其他 4 個欄位做了同樣的稽核

`has_pet`/`pet_count`/`infant_count`/`child_count` 逐一核對,結論是**這 4 個
欄位是乾淨的**,唯一有殘留標記錯誤的只有 `wants_bbq`(3 題,candidate_29/30/31,
記在 `docs/eval_v1_1_gold_pending_fixes.md`,故意沒有動凍結的私有 gold 檔案,
留給 Spencer 決定要不要連同重算 SHA 一起處理)。

但盤查 `pet_count` 的失敗案例時,candidate_25/candidate_26 這兩題浮出一個
異常模式:客人第一句話就把日期、人數、寵物狀態講得清清楚楚,`checkin_date`/
`checkout_date`/`adult_count`/`pet_count` 卻**全部**是 `None`——不是某個
欄位解析錯,是整輪資訊完全沒進到 `conversation_states`。

## 根因:跟 0cba8d7 修的是同一類問題,但是沒被涵蓋到的新變體

客人的第一句話長這樣:

```
彭璟蕙
[PHONE]
8／8-8／9
6位
無寵物
```

單獨拿去 `parse_inquiry()` 解析,日期/人數/寵物**全部解析正確**。問題出在
**意圖分類**這一層:

1. `app/domain/form_reply_detector.py` 的 `looks_like_structured_form_reply()`
   要求至少 3 行要有「標籤:內容」的冒號格式才算表單回覆——這個檔案是
   0cba8d7 那次全新建立的,commit message 跟範例全部針對「聯絡人：」「入住
   日期：」這種**有標籤**的 LINE OA 自動回覆模板。這句話一行標籤都沒有,
   直接判定「不是表單回覆」。
2. 沒被表單偵測器接住,就往下走到 FAQ 比對:「無寵物」命中 `pets` 這個
   tier-1 FAQ topic。
3. `pets` 不是 `is_booking_equivalent_topic`(這個判斷本身是刻意的、有測試
   鎖定,`tests/test_faq_matcher.py::test_only_whole_house_is_booking_equivalent`
   明講只有 `whole_house` 算),所以即使這句話帶著完整訂房日期,整句話還是
   被鎖死成純 `faq` 意圖。
4. 意圖不是 quote-relevant,`ConversationStateService._open_if_inquiry()`
   的閘門過不了,這整輪訂房資訊從頭到尾沒有被存進資料庫。

0cba8d7 當時解決的是「有標籤的表單被 FAQ 關鍵字劫持」,這次是同一個劫持機制的
「無標籤」變體——當時的真實客人資料裡沒出現過這個樣式,不是被考慮過又刻意排除,
是根本沒被涵蓋到。eval 用的是匿名化的真實客人對話集,這次才第一次讓它現形。

## 修法

在 `looks_like_structured_form_reply()` 裡新增一條平行判斷:多行(≥3 行)、
每行短(≤20 字,像單一欄位值而非完整句子)、不含問句特徵(問號/請問/想問/嗎)、
且整段解得出真實日期或人數——同時成立才算「無標籤表單回覆」,直接複用
`parse_inquiry_intent` 裡本來就有、且 FAQ 比對之前就會攔截的
`booking_question` 分支,完全不用碰 `is_booking_equivalent_topic`(避免跟
既有測試鎖定的設計衝突)。

新增 `tests/test_form_reply_detector.py`(這個模組先前完全沒有專屬測試),涵蓋
真實 regression case、含問句特徵不誤判、行數不足不誤判、長句子拖累計數不誤判、
無日期/人數訊號不誤判。`tests/test_inquiry_intent.py` 補上對應的整合案例。

## 驗證結果

用 eval v1.1(`villa_eval_private/eval_v1/expanded_gold_50_v1_1.jsonl`)重跑
確認:candidate_25/candidate_26 完全轉正,不再出現在失敗清單。整體 case-level
通過率從 20/50 (40.0%) 上升到 22/50 (44.0%)。

## 心得

這次的發現順序值得記一筆:**目標是修 eval 的計分邏輯,結果盤查標記一致性的
副產品挖到一個更嚴重的真實 bug**。這印證了一件事——eval 不是只有「跑分數」的
價值,拿真實客人對話集去核對系統的每一個判斷分支,本身就是壓力測試。0cba8d7
那次修正涵蓋了當時看得到的所有真實案例,但「真實案例」的樣本本來就是有限的;
eval 資料集擴充的每一批新案例,某種程度上都是在幫忙補齊「還沒被踩過的邊界」。

## 相關 commit

- `6afb0b8` — eval `wants_bbq`/`has_pet` tri-state 計分修正(起點)
- `docs/eval_v1_1_gold_pending_fixes.md` — 待處理的 3 題 gold 標記修正 + 本次
  發現的完整根因記錄(先於這個 commit 寫成文件)
- 本次(`looks_like_structured_form_reply` 無標籤表單修正)——待 commit
