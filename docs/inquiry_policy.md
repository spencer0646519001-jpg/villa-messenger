# Inquiry Policy

## Purpose

V1.5 inquiry handling should help staff respond quickly while staying conservative. Guest replies are preliminary only and must avoid confirming availability, booking status, or final price.

## Guest Inquiry Parsing

Future parsing should identify these fields when present:

- Check-in date.
- Checkout date.
- Number of nights.
- Adults.
- Children.
- Infants.
- Pets.

If required details are missing, ask concise follow-up questions instead of guessing.

## Guest Reply Rules

Guest-facing replies must:

- Use whole-house preliminary pricing language.
- Avoid separate room pricing.
- Include the standard availability and final-price confirmation sentence.
- Ask for missing details when needed.
- Mention staff confirmation when children, infants, pets, BBQ, or over-capacity requests are involved.

Guest-facing replies must never:

- Guarantee availability.
- Say the booking is confirmed.
- Say a room has been reserved.
- Process or request automated payment.

Required sentence for every preliminary quote:

> 實際是否有空房與最終價格，仍會由民宿人員和您確認。

Required sentence when children or infants are mentioned:

> 小孩是否需依實際佔床情況調整，最終價格仍會請民宿人員和您確認。

## Owner Notification

Future owner notifications should summarize the inquiry in a staff-friendly format. They should make uncertainty visible, including missing dates, missing guest count, children or infants, pets, BBQ, over-capacity requests, and any reason staff confirmation is required.

## Owner Slash Commands

Planned commands:

- `/幫助`
- `/詢價`
- `/今日詢價`
- `/查詢價 <inquiry_id>`
- `/未處理`
- `/緊急`
- `/昨晚總覽`
- `/今天總覽`
- `/綁定 <message_id> <booking_code>`
- `/查訂房 <booking_code>`
- `/查客人 <message_id>`
- `/解除綁定 <message_id>`

Rules:

- Only tenant owners may use slash commands.
- Owner messages that do not start with `/` must not be intercepted.
- Guest messages that start with `/` must not expose owner command behavior.

## Staff Handoff

Any case that cannot be quoted conservatively should be routed to staff follow-up. This includes more than 16 people, unclear dates, unclear guest count, missing pet count, special requests, and anything outside configured tenant policy.

## 新增:客滿日處理規則(V1.5,搭配 Google Calendar)

### 判斷邏輯

對於客人詢問的每一個日期區間:

1. 查詢 Google Calendar,取得區間內所有事件
2. 對每個事件,檢查 `event.summary` 是否「包含」tenant config 裡的 `google_calendar.booking_keywords` 任一個關鍵字
3. 任何一晚命中 → 整段視為「客滿」,不報價
4. 全部未命中 → 照常進入報價流程

### 客滿時的回覆

不報價,改用「客滿模板」回客人:

> 您好,您詢問的日期目前可能已有訂房,需請民宿人員和您確認是否仍有空房。

不提具體哪一晚客滿(因為系統可能是看錯關鍵字、不是真客滿)。
不提具體價格。
推播主人「有客人詢問 X-Y 日,系統判定為客滿,請確認」。

### 非客滿時的回覆

走原本的報價流程。**不在報價訊息中提及空房狀態**——系統不知道沒看到標記就是有房,所以不講。

### 關鍵字配置

每個 tenant 在 `config.json` 的 `google_calendar.booking_keywords` 設定一個陣列,可放多個 alias:

```json
"google_calendar": {
  "v1_5_enabled": true,
  "booking_keywords": ["枕"]
}
```

任何一個 alias 命中(子字串比對)就算客滿。

## 新增:OperationMode(V1.5 手動,V2 自動排程)

系統有兩種模式:

- **Off(預設)**:解析、計算、存資料庫都正常運作,但**不主動回覆客人**,也**不推播主人**(因為主人正在處理)
- **On**:夜間或主人離線時啟動。詢價走報價/客滿/補資料模板回覆;非詢價推播主人;緊急任何時段推主人

切換方式:
- V1.5:主人用 `/開機` `/關機` 切換
- V2:加上自動時間排程,主人可設定預設 On 時段

任何模式下,**緊急訊息一律推播主人**。緊急偵測不受 Off 影響。

## 新增:Urgency 偵測

對每一則進來的客人訊息,在 parser 後加一道 urgency 檢查。命中關鍵字 → 標記為 urgent → 立即推播主人(任何時段)+ 系統不自動回。

關鍵字清單(V1.5 起始版,可配置):

- 水:沒水、停水、漏水、淹水
- 電:停電、跳電、沒電
- 瓦斯:瓦斯味、漏氣
- 熱水:沒熱水、熱水器
- 冷暖:冷氣壞、冷氣不冷
- 門:鎖頭、開不了門、鑰匙
- 衛浴:馬桶堵、馬桶不通
- 安全:小偷、闖入、受傷、火、煙
