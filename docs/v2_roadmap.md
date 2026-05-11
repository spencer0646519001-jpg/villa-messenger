# V2 Roadmap

V1.5 完成後的下一波。主要圍繞「降低人工依賴」與「更好的訊息覆蓋」。

## V2 預計加入

### 1. RAG FAQ
讓系統能直接回答低風險、結構化的客人問題,例如:
- WiFi 密碼、停車位置、check-in 時間
- 寵物政策、有無早餐
- 周邊景點、交通方式

關鍵限制:
- 只命中明確「FAQ 白名單」才啟動 RAG
- 沒把握的問題(信心門檻不足)一律 fallback 到「轉人工」
- 不處理任何涉及訂房、空房、價格的問題(這些走詢價流程)

### 2. 時間排程 On/Off
V1.5 的 On/Off 是手動指令(主人打 `/開機` `/關機`)。
V2 加上自動排程,例如「每天 22:00 自動 On,隔天 08:00 自動 Off」。
時段每個 tenant 可自定。手動指令仍可即時覆寫排程。

### 3. Taiwan Holiday API
V1.5 是把 2026 國定假日手動寫進 `special_dates`。
V2 改成自動抓 Taiwan holiday API,每年自動更新,主人不用手動編日期。

### 4. Messenger API
V1.5 用 Meta Business Suite 內建罐頭回覆撐著。
V2 接 Messenger Platform API,讓 Messenger 也走完整詢價流程。

### 5. Owner 操作面板(基礎版)
網頁版簡單 dashboard:
- 看今日詢價
- 看緊急訊息
- 手動 On/Off
- 編輯 FAQ 內容
- 看 Google Calendar 沒標記的日期(輔助記錄)

非 SaaS 級別。一個 tenant 一個帳號,登入靠簡單的 token,不做 RBAC。

## V2 不會做的事

留給 V3:
- 多租戶管理介面(讓舅舅自己建帳號、自己編價格)
- 完整登入/權限系統、audit log
- 自動收訂金、串金流
- 完全 AI 對話模式
