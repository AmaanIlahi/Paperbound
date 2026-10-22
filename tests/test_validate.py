from types import SimpleNamespace

import pytest

from paperbound.pipeline.validate import (
    field_confidence,
    ground_quote,
    validate_fields,
    value_in_quote,
)
from paperbound.schemas.lease import LeaseFields


def make_page(page_no, text, quality=1.0):
    """Lay words out on a grid: 10pt wide each, 12pt rows of 8 words."""
    words = []
    for i, w in enumerate(text.split()):
        x, y = (i % 8) * 50, (i // 8) * 12
        words.append({"text": w, "bbox": [x, y, x + 40, y + 10]})
    return SimpleNamespace(page_no=page_no, words=words, quality_score=quality, text=text)


PAGE1 = make_page(1, "This Lease is made between Harborview Properties LLC and Brightline Studio Inc. as of March 15, 2024.")
PAGE2 = make_page(
    2,
    "The Term shall commence on April 1, 2024 and expire on March 31, 2027. "
    "Tenant shall pay base rent of $8,500.00 per month in advance.",
)
PAGES = [PAGE1, PAGE2]


def test_exact_match_recovers_union_bbox():
    score, page_no, bboxes = ground_quote("Harborview Properties LLC", PAGES, preferred_page=1)
    assert score == 1.0
    assert page_no == 1
    # words 5, 6, 7 of page 1 start at x=250, 300, 350 (each 40 wide), same row -> one line box
    assert bboxes == [[250, 0, 390, 10]]


def test_fuzzy_match_tolerates_ocr_style_noise():
    score, page_no, bboxes = ground_quote("base rent of $8,5OO.00 per month", PAGES, preferred_page=2)
    assert 0.8 <= score < 1.0
    assert page_no == 2
    assert bboxes


def test_no_match_scores_low():
    score, _, _ = ground_quote("The tenant must install a rooftop helipad", PAGES, preferred_page=1)
    assert score < 0.8


def test_wrong_page_is_recovered_and_flagged():
    lease = make_lease(rent=("8500.0", "base rent of $8,500.00 per month", 1))
    fields = {f.name: f for f in validate_fields(lease, PAGES)}
    rent = fields["rent_amount"]
    assert rent.page_no == 2
    assert "page_corrected" in rent.flags
    assert "ungrounded" not in rent.flags


def make_lease(rent=None, start=None, end=None, notice=None):
    def f(value, quote=None, page=None):
        return {"value": value, "source_quote": quote, "page": page}

    rent = rent or (None, None, None)
    start = start or (None, None, None)
    end = end or (None, None, None)
    notice = notice or (None, None, None)
    return LeaseFields.model_validate(
        {
            "landlord": f("Harborview Properties LLC", "Harborview Properties LLC", 1),
            "tenant": f(None),
            "premises_address": f(None),
            "start_date": f(start[0], start[1], start[2]),
            "end_date": f(end[0], end[1], end[2]),
            "rent_amount": f(float(rent[0]) if rent[0] else None, rent[1], rent[2]),
            "rent_period": f(None),
            "notice_period_days": f(int(notice[0]) if notice[0] else None, notice[1], notice[2]),
        }
    )


def test_confidence_math():
    assert field_confidence(1.0, True, 1.0) == 1.0
    assert field_confidence(1.0, True, 0.5) == pytest.approx(0.95)
    assert field_confidence(0.9, False, 1.0) == pytest.approx(0.64)
    assert field_confidence(0.0, False, 0.0) == 0.0


def test_grounded_field_gets_full_confidence_and_bbox():
    fields = {f.name: f for f in validate_fields(make_lease(), PAGES)}
    landlord = fields["landlord"]
    assert landlord.bbox is not None
    assert landlord.bboxes == [landlord.bbox]
    assert landlord.flags == []
    assert landlord.confidence == 1.0


def test_missing_value_is_not_found_with_zero_confidence():
    fields = {f.name: f for f in validate_fields(make_lease(), PAGES)}
    assert fields["tenant"].flags == ["not_found"]
    assert fields["tenant"].confidence == 0.0


def test_ungrounded_quote_clears_bbox_and_lowers_confidence():
    lease = make_lease(rent=("8500", "rent is eight grand a month, payable by wire", 2))
    rent = {f.name: f for f in validate_fields(lease, PAGES)}["rent_amount"]
    assert "ungrounded" in rent.flags
    assert rent.bbox is None
    assert rent.confidence < 0.5


def test_value_not_in_quote_is_flagged():
    lease = make_lease(rent=("9000", "base rent of $8,500.00 per month", 2))
    rent = {f.name: f for f in validate_fields(lease, PAGES)}["rent_amount"]
    assert "value_not_in_quote" in rent.flags
    assert "ungrounded" not in rent.flags
    assert rent.confidence == pytest.approx(0.6 + 0.0 + 0.1)


@pytest.mark.parametrize(
    "name,value,quote,expected",
    [
        ("rent_amount", 8500.0, "initial rate of Eight Thousand Five Hundred Dollars ($8,500.00) per month", True),
        ("rent_amount", 8755.0, "initial rate of ($8,500.00) per month", False),
        ("end_date", "2027-03-31", "expiring at 11:59 p.m. on March 31, 2027", True),
        ("start_date", "2024-04-01", "commencing on April 1, 2024", True),
        ("start_date", "2024-04-01", "commencing on 04/01/2024", True),
        ("end_date", "2027-03-31", "commencing on April 1, 2024", False),
        ("notice_period_days", 90, "at least ninety (90) days before", True),
        ("notice_period_days", 90, "at least ninety days before", True),
        ("notice_period_days", 120, "within one hundred twenty days", True),
        ("notice_period_days", 60, "at least ninety (90) days before", False),
        ("rent_period", "monthly", "$8,500.00 per month", True),
        ("landlord", "Harborview Properties LLC", "by and between Harborview Properties LLC, a New York", True),
        ("landlord", "Acme Corp", "by and between Harborview Properties LLC, a New York", False),
    ],
)
def test_value_in_quote(name, value, quote, expected):
    assert value_in_quote(name, value, quote) is expected


def test_range_checks():
    lease = make_lease(
        start=("2027-03-31", "expire on March 31, 2027", 2),
        end=("2024-04-01", "commence on April 1, 2024", 2),
        rent=("-5", "base rent of $5", 2),
        notice=("400", "four hundred (400) days", 2),
    )
    fields = {f.name: f for f in validate_fields(lease, PAGES)}
    assert "end_before_start" in fields["start_date"].flags
    assert "end_before_start" in fields["end_date"].flags
    assert "invalid_amount" in fields["rent_amount"].flags
    assert "invalid_notice_period" in fields["notice_period_days"].flags


def test_bad_date_string_is_flagged():
    lease = make_lease(end=("31/03/2027", "expire on March 31, 2027", 2))
    end = {f.name: f for f in validate_fields(lease, PAGES)}["end_date"]
    assert "invalid_date" in end.flags
