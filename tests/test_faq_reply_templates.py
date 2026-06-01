"""
Unit tests for the STAGE D render_faq_* templates.

Tier-1 answers must be CONFIG-DRIVEN (flipping a config value changes the
sentence) and must NOT contain the "已通知" claim. Tier-2 / fallback answers are
confirm-and-defer: the "已通知" close appears ONLY when notified=True.
"""

from app.domain.reply_templates import (
    render_faq_breakfast,
    render_faq_checkout,
    render_faq_fallback,
    render_faq_parking,
    render_faq_pets,
    render_faq_wifi,
)

_NOTIFIED = "已通知服務人員"


# ---- tier 1: config-driven, no "已通知" -------------------------------------


def test_breakfast_answer_tracks_config_value() -> None:
    provided = render_faq_breakfast(breakfast_provided=True)
    not_provided = render_faq_breakfast(breakfast_provided=False)
    assert provided != not_provided  # flipping the config flips the sentence
    assert "有提供早餐" in provided
    assert "沒有提供早餐" in not_provided
    assert _NOTIFIED not in provided and _NOTIFIED not in not_provided


def test_checkout_answer_embeds_config_times() -> None:
    text = render_faq_checkout(check_in_after="15:00", checkout_before="11:00")
    assert "15:00" in text
    assert "11:00" in text
    assert _NOTIFIED not in text
    # Change the config value -> the rendered sentence changes.
    other = render_faq_checkout(check_in_after="14:00", checkout_before="10:00")
    assert "10:00" in other and "11:00" not in other


def test_pets_answer_embeds_config_fee_and_scope() -> None:
    allowed = render_faq_pets(
        allowed_with_notice=True, small_dogs_only=True, fee_twd_per_pet=500
    )
    assert "500" in allowed
    assert "小型犬" in allowed
    assert _NOTIFIED not in allowed
    # fee tracks config
    assert "800" in render_faq_pets(
        allowed_with_notice=True, small_dogs_only=False, fee_twd_per_pet=800
    )


def test_pets_not_allowed_when_config_disallows() -> None:
    text = render_faq_pets(allowed_with_notice=False, small_dogs_only=True, fee_twd_per_pet=500)
    assert "暫不接受寵物" in text
    assert "500" not in text  # no fee claimed when pets aren't accepted


# ---- tier 2 / fallback: "已通知" only when notified -------------------------


def test_wifi_claims_notified_only_when_notified() -> None:
    assert _NOTIFIED in render_faq_wifi(notified=True)
    assert _NOTIFIED not in render_faq_wifi(notified=False)
    assert "WiFi" in render_faq_wifi(notified=True)


def test_parking_claims_notified_only_when_notified() -> None:
    assert _NOTIFIED in render_faq_parking(notified=True)
    assert _NOTIFIED not in render_faq_parking(notified=False)
    assert "停車" in render_faq_parking(notified=False)


def test_fallback_claims_notified_only_when_notified() -> None:
    assert _NOTIFIED in render_faq_fallback(notified=True)
    assert _NOTIFIED not in render_faq_fallback(notified=False)
    # the softer failed-push wording does not assert prior notification
    assert "會再請服務人員" in render_faq_fallback(notified=False)
