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
OWNER_PUSH_URGENT_PREFIX: Final = "【緊急】客人訊息:"
OWNER_PUSH_UNCATEGORIZED_PREFIX: Final = "非詢價訊息,待處理:"

WEEKDAY_ZH: Final = ["一", "二", "三", "四", "五", "六", "日"]

PRICE_TYPE_LABEL: Final = {
    "weekday": "平日房價",
    "saturday": "週六房價",
    "summer_weekday": "暑假平日房價",
    "summer_saturday_or_holiday": "暑假/假日房價",
    "spring_festival": "春節房價",
    "national_holiday": "國定假日房價",
}
