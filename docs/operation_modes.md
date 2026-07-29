# Operation Modes

系統有兩種運作模式(On / Off),目的是不干擾民宿主白天跟客人的正常溝通。除了
tenant 層級的 On/Off,系統另有 per-customer 的人工接管暫停機制,避免自動排程
接手時打斷正在進行中的人工對話(見下方「人工接管暫停」)。

## Off 模式(預設)

系統「在背景觀察」,但不主動跟客人講話。

行為:
- 客人訊息進來 → 照樣解析、存資料庫
- **不主動回覆客人**
- **不推播主人**(因為主人此時在線上,LINE 通知本來就會響)
- 緊急訊息**例外**:任何時段都推主人

適用場景:
- 民宿主在白天工作時段
- 民宿主想暫時自己處理客人
- 系統剛部署、還在觀察階段

## On 模式

系統接手,代替主人做夜間/離線時段的初步回應。

行為:
- 詢價 → 走報價/客滿/補資料模板回覆客人
- 非詢價 → 不回覆,推播主人「明早處理」
- 緊急 → 立即推主人 + 系統不回

適用場景:
- 民宿主睡前手動 `/開機`
- 民宿主出門、不方便回訊息時手動 `/開機`

## 切換方式

### 手動指令

主人在 LINE 打:
- `/開機` → 進入 On 模式(至下一次排程邊界前有效)
- `/關機` → 進入 Off 模式(至下一次排程邊界前有效)
- `/狀態` → 查目前模式

切換立即生效,記錄在 tenant 的 `operation_state` 欄位。

### 自動排程(已上線,但目前所有 tenant 共用同一個預設時段)

`tenant_operation_state` 表結構上是 per-tenant 的(`auto_on_start_time` /
`auto_on_end_time` 欄位,schema 預設 `23:00` / `08:00`),排程邊界由
`app/domain/operation_mode_resolver.py` 的 `resolve_effective_mode` /
`compute_next_schedule_boundary` 計算。手動指令隨時覆寫排程,直到下一個排程
邊界為止。

`app/services/operation_mode_service.py::set_schedule_window()` 已經可以改這兩
個值,**但目前沒有任何 LINE 指令或 API route 會呼叫它**——沒有讓 tenant 自己
調整時段的介面。實務上所有 tenant 現在都固定在 schema 預設的 23:00–08:00,要
改只能直接寫資料庫。

## 人工接管暫停(per-customer,已上線)

自動排程只看「現在幾點」,不知道某位客人是否正被主人手動處理中 —— 這曾造成
23:00 開機瞬間打斷進行中的人工對話(見
`docs/case_study_intent_and_handoff.md`)。修法是加入 per-customer 的暫停旗標:

- 主人在 LINE 打 `/<客人顯示名稱>`(例如 `/Wendy`)→ 切換該位客人的自動回覆
  暫停/恢復,與 tenant 層級的 On/Off 無關。
- 暫停一律撐滿「接下來的整個開機時段」,不會在排程邊界(例如 23:00)提早失效
  ——即使是白天暫停,也保護到隔天 08:00 排程結束為止
  (`compute_next_active_window_end`)。
- 同名客人有 2 位以上近期發過訊息時,系統回覆候選清單請主人指定,不做無根據的
  猜測。
- 相關表:`conversation_manual_holds`。服務層:
  `app/services/conversation_handoff_service.py`。

## 舊資料重新確認(已上線)

若某對話的欄位是在 off/暫停期間累積的,系統開機後超過 20 分鐘才回覆的話,會先
送出一次性的軟性提醒(`RECONFIRM_STALE_CONTEXT_MESSAGE`),避免用可能過時的資訊
直接報價;20 分鐘內視為自然接續,直接放行。

## 關機期間漏接彙整(已上線)

`messages.handled` 欄位會標記訊息是否已有人（系統或主人）處理過。主人可隨時打
`/待回覆` 查詢尚未處理的訊息;系統另有每 5 分鐘檢查一次的背景任務,每個
tenant-local day 最多發一次彙整推播,避免關機時段的訊息完全沒人知道
(`run_nightly_digest_check`,`app/main.py` lifespan)。

## 重要保證

- **Off 模式下,緊急訊息仍會推主人**——緊急偵測不受 OperationMode 影響
- **Off ↔ On 切換不會丟失訊息**——所有訊息任何模式下都存進資料庫
- **主人主動發訊息給客人時,系統一律不攔截**——不管 On 或 Off
