import pytest

from app.domain.guest_count_parser import parse_guest_counts


@pytest.mark.parametrize(
    ("text", "expected_count"),
    [
        ("4人", 4),
        ("四人", 4),
        ("4位", 4),
        ("總共4位", 4),
        ("請問暑假四個人多少錢", 4),
    ],
)
def test_parse_total_guest_count(text: str, expected_count: int) -> None:
    result = parse_guest_counts(text)

    assert result.guest_count == expected_count
    assert result.adult_count == expected_count
    assert result.child_count is None
    assert result.confidence == "high"


@pytest.mark.parametrize(
    "text",
    [
        "8～12人", "8-12人", "8到12人", "8~12位", "20人左右", "20人上下",
        "大概20人", "差不多20人",
        # Codex review of commit 115c28b (P2): unit-bearing lower bound and
        # 至 as a separator used to fall through to a firm 8 or 12.
        "8人～12人", "8位到12位", "8至12人",
    ],
)
def test_ranged_or_approximate_total_guest_count_stays_unresolved(text: str) -> None:
    # eval failure_681/failure_682/failure_558 regression: a ranged or
    # approximate total count used to silently resolve to one end of the
    # range / the approximate figure instead of staying unresolved so the
    # reply can ask for a firm number.
    result = parse_guest_counts(text)

    assert result.guest_count is None
    assert result.adult_count is None


@pytest.mark.parametrize(
    ("text", "adults", "children", "total"),
    [
        ("2大2小", 2, 2, 4),
        ("兩大兩小", 2, 2, 4),
        ("大人2位小孩1位", 2, 1, 3),
        ("8大2小", 8, 2, 10),
        ("12大2小", 12, 2, 14),
    ],
)
def test_parse_adults_and_children(text: str, adults: int, children: int, total: int) -> None:
    result = parse_guest_counts(text)

    assert result.adult_count == adults
    assert result.child_count == children
    assert result.guest_count == total
    assert result.needs_child_confirmation is True
    assert result.confidence == "high"


def test_infants_are_not_counted_as_guests() -> None:
    result = parse_guest_counts("2大1嬰兒")

    assert result.adult_count == 2
    assert result.infant_count == 1
    assert result.guest_count == 2
    assert result.needs_infant_confirmation is True
    assert result.confidence == "high"


def test_missing_guest_count_is_low_confidence() -> None:
    result = parse_guest_counts("請問還有房嗎")

    assert result.guest_count is None
    assert result.confidence == "low"


def test_number_wei_label_word_order() -> None:
    # "N位大人/小孩/嬰兒" -- number-然後-"位"-然後-label, distinct from the
    # already-covered "大人N位" / "N大人" orders.
    result = parse_guest_counts("8位大人1位嬰兒")

    assert result.adult_count == 8
    assert result.infant_count == 1
    assert result.guest_count == 8
    assert result.needs_infant_confirmation is True
    assert result.confidence == "high"


def test_number_wei_child_word_order() -> None:
    result = parse_guest_counts("2位大人3位小孩")

    assert result.adult_count == 2
    assert result.child_count == 3
    assert result.guest_count == 5


def test_label_theft_guard_survives_whitespace_gap() -> None:
    # Regression: a space between 大人's own "2位" and the following 小孩
    # label used to break the label-theft guard (it only matched labels
    # directly adjacent to the stolen number), letting 小孩 steal the "2"
    # that already belongs to 大人 instead of reading its own "1位".
    result = parse_guest_counts("大人 2位小孩 1位")

    assert result.adult_count == 2
    assert result.child_count == 1
    assert result.guest_count == 3
