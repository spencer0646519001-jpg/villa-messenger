# Pricing Rules

## Quote Scope

All guest-facing quotes are whole-house preliminary prices. Do not describe pricing as separate room pricing.

Every quote must clearly state that this is a system-generated preliminary estimate and that actual availability and final price will be confirmed by homestay staff:

> 此為系統依目前規則初步估算，實際空房與最終價格仍會請民宿人員和您確認。

The system must never say that a room is available, reserved, or booked.

## Base Whole-House Pricing

Prices are in TWD per night.

The price tier is chosen by the guest's requested room count, not by guest count.
The legacy price keys remain the same so config values do not need to move.

| Room count | Price key | Standard capacity | Weekday Sunday-Friday | Saturday | Summer weekday | Summer Saturday or national holiday | Spring festival |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 rooms | `8_people` | 8 people | 9,000 | 15,000 | 12,000 | 15,000 | 30,000 |
| 3 rooms | `10_people` | 10 people | 12,000 | 18,000 | 15,000 | 18,000 | 30,000 |
| 4 rooms | `12_people` | 12 people | 15,000 | 21,000 | 18,000 | 21,000 | 30,000 |

Spring festival is one flat rate across all three room counts.

## Room Count Pricing

- If date, guest count, and pet slots are complete but room count is missing, ask which room count the guest wants before quoting.
- 1 room: do not quote automatically; require staff confirmation.
- 2 rooms: use the `8_people` whole-house price, standard capacity 8.
- 3 rooms: use the `10_people` whole-house price, standard capacity 10.
- 4 rooms: use the `12_people` whole-house price, standard capacity 12.
- 13 to 16 people with 4 rooms: use the 4-room price plus NT$1,000 per person over 12.
- If the chosen room count cannot fit the guest count, ask whether to change to the minimum room count that can fit the party.
- More than 16 people: do not quote automatically; require staff confirmation.

## Children And Infants

Children should be counted as guests for the preliminary estimate.

Infants are not automatically counted as guests for pricing.

If children are mentioned, staff confirmation is required and the guest-facing reply must include:

> 小孩是否需依實際佔床情況調整，最終價格仍會請民宿人員和您確認。

If infants are mentioned, staff confirmation is required and the guest-facing reply must include:

> 嬰兒是否需依實際佔床情況調整，最終價格仍會請民宿人員和您確認。

## Pets

- Pets are allowed with notice.
- `毛孩` usually means dog.
- Small dogs only for now.
- Pet fee: NT$500 per pet.
- Pet fee is per pet per stay, not per night.
- Pets are not counted as guests.
- If pet count is missing, ask the guest to provide pet count.
- Do not guarantee pet acceptance beyond the stated policy.

Pet-related replies must still include the standard final confirmation sentence.

## Long Stay Discount

- 1 night: no discount.
- 2 nights: NT$1,000 discount.
- 3 nights: NT$2,000 discount.
- N nights: `(N - 1) * 1000` discount.

Formula:

```text
max(0, nights - 1) * 1000
```

**Peak dates do not discount.** If ANY night of the stay is priced as
`spring_festival` or `national_holiday`, the long stay discount is zero for the
whole stay -- a trip that includes a holiday is a peak trip. Saturday and summer
rates are ordinary pricing and still discount normally.

## Season And Date Rules

- Summer is July and August.
- Saturday pricing applies only to the Saturday night itself, not the entire stay.
- Other nights are priced by their own date.
- National holidays and spring festival are loaded from the Taiwan government
  working-day calendar, fetched per year and cached in `holiday_calendar_cache`.
  Each tenant's `special_dates` in config.json is merged in on top as a
  hand-maintained override.
- Derivation rule (`app/domain/holiday_calendar.py`): take the non-working days,
  cut them into consecutive runs; a run containing any named day is a national
  holiday in full, and a run whose names mention 春節 / 除夕 / 小年夜 is also a
  spring festival in full. A run of unnamed days is just a weekend.
- **If the calendar for a year the stay touches is unavailable, the system does
  not quote.** It replies with the standard staff-confirmation line and pushes
  the owner. A missing calendar must never be priced as "that year has no
  holidays" -- that is what quoted 2027 春節 at the weekday rate.
- Make-up workdays (補班日) are derived but do not affect pricing: a working
  Saturday is still a Saturday to a guest.

## Price Type Priority

When more than one date rule could apply, use this priority:

1. `spring_festival`
2. `summer_saturday_or_holiday`
3. `summer_weekday`
4. `saturday`
5. `weekday`

National holidays in V1.5 map to the `summer_saturday_or_holiday` price key.

## BBQ

If guests mention BBQ, note that BBQ requires advance notice and the cleaning fee is NT$1,000.

BBQ follows the same mechanism as the pet fee: the NT$1,000 cleaning fee is
added to the quoted total automatically, and BBQ is also flagged in
`requires_owner_confirmation` so the owner still confirms it manually.

## Deposits

- Booking deposit: 30% of total room price.
- Equipment/security deposit on arrival: NT$3,000.

V1.5 must not process deposits or payments automatically.
