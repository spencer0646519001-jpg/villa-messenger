from app.domain.form_reply_detector import looks_like_structured_form_reply


def test_labeled_form_reply_still_matches() -> None:
    text = (
        "聯絡人:林小姐\n"
        "聯絡電話:0912345678\n"
        "入住日期:8/15\n"
        "入住人數:8位大人1位嬰兒\n"
        "是否有寵物(僅限小型寵物,每隻酌收NT500):否\n"
        "是否烤肉(酌收清潔費NT1,000):是\n"
        "幾台車:2-3台"
    )
    assert looks_like_structured_form_reply(text) is True


def test_unlabeled_form_reply_matches_real_regression_case() -> None:
    # eval candidate_25/26 (villa_eval_private/eval_v1/expanded_gold_50_v1_1.jsonl):
    # this exact line-by-line, no-label reply used to fall through to FAQ-topic
    # matching (hit the "pets" topic via 無寵物, which isn't booking-equivalent)
    # and get locked into inquiry_type="faq", so the whole turn's dates/guest
    # count/pet status never reached conversation_states.
    text = "彭璟蕙\n[PHONE]\n8/8-8/9\n6位\n無寵物"
    assert looks_like_structured_form_reply(text) is True


def test_unlabeled_short_question_is_not_a_form_reply() -> None:
    text = "8/8-8/9\n請問\n還有房嗎"
    assert looks_like_structured_form_reply(text) is False


def test_unlabeled_too_few_lines_is_not_a_form_reply() -> None:
    text = "8/8-8/9\n6位"
    assert looks_like_structured_form_reply(text) is False


def test_unlabeled_long_sentence_line_does_not_count_toward_threshold() -> None:
    # 3 lines total, but one reads as a real sentence (well over the short-line
    # cap) -- only 2 lines qualify as bare answers, under the 3-line minimum.
    text = "8/8-8/9\n6位\n這次是我第二次來,上次住得很開心還會再回來"
    assert looks_like_structured_form_reply(text) is False


def test_no_date_or_guest_signal_is_not_a_form_reply() -> None:
    text = "彭璟蕙\n[PHONE]\n無寵物\n沒有其他問題"
    assert looks_like_structured_form_reply(text) is False
