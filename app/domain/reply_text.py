from typing import Final

SAFETY_NOTE: Final = (
    "此為系統依目前規則初步估算,"
    "實際空房與最終價格仍會請民宿人員和您確認。"
)

CHILDREN_CONFIRMATION: Final = (
    "由於有小孩同行,小孩是否需依實際佔床情況調整,"
    "最終價格仍會請民宿人員和您確認。"
)

INFANTS_CONFIRMATION: Final = (
    "由於有嬰兒同行,嬰兒是否需依實際佔床情況調整,"
    "最終價格仍會請民宿人員和您確認。"
)

PETS_CONFIRMATION: Final = (
    "寵物清潔費為每隻 NT$500。"
    "實際是否接受寵物入住,仍需民宿人員和您確認。"
)

QUOTE_GREETING: Final = "您好,以下為系統依目前規則初步估算的報價:"

FULL_HOUSE_MESSAGE: Final = (
    "您好,您詢問的日期目前可能已有訂房,"
    "需請民宿人員和您確認是否仍有空房。"
)

OVER_CAPACITY_MESSAGE: Final = (
    "您好,您詢問的人數超過我們的最大可容納人數(16 人),"
    "這部分需要請民宿人員直接和您確認方案,稍後會有專人回覆您。"
)

INVALID_DATE_MESSAGE: Final = (
    "您好,看起來您提供的入住日和退房日順序有些對不上,"
    "方便再確認一下完整的入住日和退房日嗎?"
)

MISSING_INFO_HEADER: Final = "您好,方便補充以下資訊嗎?"
MISSING_INFO_FOOTER: Final = "提供完整資訊後,系統可以幫您試算初步報價。"

MISSING_CHECKIN_LINE: Final = "・入住日期"
MISSING_CHECKOUT_LINE: Final = "・退房日期"
MISSING_GUEST_COUNT_LINE: Final = "・入住人數(幾大幾小)"
MISSING_PET_COUNT_LINE: Final = "・寵物隻數"

SINGLE_MISSING_CHECKOUT_MESSAGE: Final = (
    "您好,看到您詢問入住,方便提供完整的退房日嗎?"
    "例如「5/12 入住 5/14 退房」這樣的格式,系統可以幫您試算初步報價。"
)

SINGLE_MISSING_CHECKIN_MESSAGE: Final = (
    "您好,看到您詢問入住,方便提供完整的入住日嗎?"
    "例如「5/12 入住 5/14 退房」這樣的格式,系統可以幫您試算初步報價。"
)

SINGLE_MISSING_GUEST_COUNT_MESSAGE: Final = (
    "您好,方便告訴我們入住總人數嗎?"
    "例如「2 大人 1 小孩」這樣的格式,系統可以幫您試算初步報價。"
)

SINGLE_MISSING_PET_COUNT_MESSAGE: Final = (
    "您好,您提到有毛孩同行,方便告訴我們是幾隻嗎?"
    "小狗一隻 NT$500 清潔費,人數和日期確認後我們再為您試算總價。"
)

OWNER_PUSH_FULL_HOUSE_PREFIX: Final = "系統判定為客滿,請確認:"
# Friendly owner-notification format (no-name baseline, PR: owner-push rewrite).
# The header opens the push; the customer's name is OPTIONAL (rendered only when
# a display name is available -- we deliberately NEVER print the raw U... userId).
OWNER_PUSH_UNCATEGORIZED_PREFIX: Final = "📩 有客人訊息待回覆"
OWNER_PUSH_URGENT_PREFIX: Final = "📩【緊急】有客人訊息待回覆"
OWNER_PUSH_CUSTOMER_PREFIX: Final = "客人:"
OWNER_PUSH_QUESTION_PREFIX: Final = "客人問:"
OWNER_PUSH_URGENT_KEYWORDS_PREFIX: Final = "觸發關鍵字:"
# Closes must reflect what actually happened (project "claims must be true"
# standard). DEFER_CLOSE is ONLY truthful when the system replied to the customer
# (the FAQ confirm-and-defer path). When no auto-reply went out -- plain
# non-inquiry and urgent -- use a non-asserting close that calls the owner to act.
OWNER_PUSH_DEFER_CLOSE: Final = "(系統已回覆客人會請專人對接)"
OWNER_PUSH_UNREPLIED_CLOSE: Final = "(尚未回覆客人,請您接手)"
OWNER_PUSH_URGENT_CLOSE: Final = "(請盡快人工回覆)"
OWNER_PUSH_AVAILABILITY_UNVERIFIED_PREFIX: Final = (
    "系統無法驗證日期可用性,已照常報價,請人工確認空房:"
)

# ---- STAGE D: FAQ answers ------------------------------------------------
# Tier-1 (config-driven, self-contained -- NO "已通知" line; nothing is pushed).
FAQ_BREAKFAST_PROVIDED: Final = "您好,我們有提供早餐喔。"
FAQ_BREAKFAST_NOT_PROVIDED: Final = (
    "您好,我們沒有提供早餐喔,需請您自行準備或外出用餐。"
)
FAQ_PETS_NOT_ALLOWED: Final = "您好,不好意思,我們目前暫不接受寵物入住喔。"

# Tier-1 (config-driven): wifi / parking -- three branches each.
FAQ_WIFI_PROVIDED_FREE: Final = "您好,我們有提供免費 WiFi 喔。"
FAQ_WIFI_PROVIDED: Final = "您好,我們有提供 WiFi 喔,費用部分入住時服務人員會與您說明。"
FAQ_WIFI_NOT_PROVIDED: Final = "您好,不好意思,我們目前沒有提供 WiFi 喔。"
FAQ_PARKING_AVAILABLE_FREE: Final = "您好,我們有提供免費停車位喔。"
FAQ_PARKING_AVAILABLE: Final = "您好,我們有提供停車位喔,費用部分入住時服務人員會與您說明。"
FAQ_PARKING_NOT_AVAILABLE: Final = "您好,不好意思,我們目前沒有提供停車位喔。"

# Non-whitelist faq fallback = confirm-and-defer.
# NOTIFIED claims "已通知服務人員" (truthful only after a successful push);
# DEFER is the softer non-asserting line used on push failure.
FAQ_NOTIFIED_CLOSE: Final = "細節已通知服務人員,稍後會有專人與您對接。"
FAQ_DEFER_CLOSE: Final = "這部分我們會再請服務人員與您聯繫。"

FAQ_WHOLE_HOUSE: Final = (
    "您好,枕123是一次只接待一組客人的包棟民宿,"
    "整棟為您和親友獨享,適合放鬆、聚會的假期。"
)

FAQ_AMENITIES_HEADER: Final = "枕123 提供的設備:"
FAQ_AMENITIES_EMPTY: Final = "您好,設備資訊目前尚未建立,請聯繫民宿人員確認。"
FAQ_ROOM_TYPE_EMPTY: Final = "您好,房型資訊目前尚未建立,請聯繫民宿人員確認。"
FAQ_LOCATION_PREFIX: Final = "枕123 位於"
FAQ_LOCATION_EMPTY: Final = "您好,地址資訊目前尚未建立,請聯繫民宿人員確認。"

FAQ_FALLBACK_LEAD: Final = "您好,已收到您的訊息,"

WEEKDAY_ZH: Final = ["一", "二", "三", "四", "五", "六", "日"]

PRICE_TYPE_LABEL: Final = {
    "weekday": "平日房價",
    "saturday": "週六房價",
    "summer_weekday": "暑假平日房價",
    "summer_saturday_or_holiday": "暑假/假日房價",
    "spring_festival": "春節房價",
    "national_holiday": "國定假日房價",
}
