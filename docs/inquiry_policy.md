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

