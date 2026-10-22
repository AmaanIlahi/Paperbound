from datetime import date

import pytest

from paperbound.pipeline.validate import (
    quote_rule_ok,
    rent_table_amount_on,
    rent_table_rows,
    validate_fields,
    value_in_quote,
)
from tests.test_hardening import f, lease, make_page


@pytest.mark.parametrize(
    "name,quote,ok",
    [
        ("holdover_rate_percent", "a rate equal to one hundred fifty percent (150%) of the Base Rent", True),
        ("holdover_rate_percent", "a rate equal to one hundred fifty of the Base Rent", False),
        ("rent_escalation_percent", "Base Rent shall increase by three percent (3%)", True),
        ("security_deposit", "Tenant shall deposit the sum of $17,000.00 as security", True),
        ("security_deposit", "Tenant shall deposit a sum as security", False),
        ("current_rent", "3 Apr 1, 2026 - Mar 31, 2027 $9,017.65", True),
        ("notice_method", "Notice shall be delivered by certified mail or nationally recognized overnight courier", True),
        ("notice_method", "The weather is nice", False),
        ("notice_section", "11. Renewal; Termination at Expiration", True),
        ("notice_section", "Section 11. Renewal; Termination at Expiration", True),
        ("notice_section", "pursuant to Section 11 of this Lease", True),
        ("notice_section", "Tenant may elect either to renew", False),
    ],
)
def test_new_field_quote_rules(name, quote, ok):
    assert quote_rule_ok(name, quote) is ok


@pytest.mark.parametrize(
    "name,value,quote,ok",
    [
        ("holdover_rate_percent", 150.0, "one hundred fifty percent (150%) of the Base Rent", True),
        ("holdover_rate_percent", 15.0, "one hundred fifty percent (150%) of the Base Rent", False),
        ("security_deposit", 17000.0, "the sum of $17,000.00 as security", True),
        ("security_deposit", 1700.0, "the sum of $17,000.00 as security", False),
        ("notice_section", "Section 11", "11. Renewal; Termination at Expiration", True),
        ("notice_section", "Section 1", "11. Renewal; Termination at Expiration", False),
        ("notice_section", "Section 11", "as set forth in Section 16", False),
    ],
)
def test_new_field_value_must_be_in_its_quote(name, value, quote, ok):
    assert value_in_quote(name, value, quote) is ok


TABLE = (
    "Year Period Monthly Base Rent 1 Apr 1, 2024 - Mar 31, 2025 $8,500.00 "
    "2 Apr 1, 2025 - Mar 31, 2026 $8,755.00 3 Apr 1, 2026 - Mar 31, 2027 $9,017.65"
)


def test_current_rent_is_checked_against_the_rent_table_row_for_today():
    page = make_page(2, TABLE)
    today = date(2026, 10, 22)
    good = {x.name: x for x in validate_fields(lease(current_rent=f(9017.65, "3 Apr 1, 2026 - Mar 31, 2027 $9,017.65", 2)), [page], today)}
    assert "conflicting_values" not in good["current_rent"].flags
    old_row = "2 Apr 1, 2025 - Mar 31, 2026 $8,755.00"
    wrong = {x.name: x for x in validate_fields(lease(current_rent=f(8755.0, old_row, 2)), [page], today)}["current_rent"]
    assert "conflicting_values" in wrong.flags and wrong.confidence <= 0.7
    # the same value is fine on a date when that row is the current one
    earlier = {x.name: x for x in validate_fields(lease(current_rent=f(8755.0, old_row, 2)), [page], date(2025, 6, 1))}
    assert "conflicting_values" not in earlier["current_rent"].flags


def test_rent_table_rows_are_parsed():
    page = make_page(2, "1 Apr 1, 2024 - Mar 31, 2025 $8,500.00 2 Apr 1, 2025 - Mar 31, 2026 $8,755.00")
    assert rent_table_rows([page]) == [
        (date(2024, 4, 1), date(2025, 3, 31), 8500.0),
        (date(2025, 4, 1), date(2026, 3, 31), 8755.0),
    ]
    assert rent_table_amount_on([page], date(2025, 12, 1)) == 8755.0
    assert rent_table_amount_on([page], date(2030, 1, 1)) is None


def test_invalid_percent_is_flagged():
    page = make_page(1, "a rate equal to one hundred fifty percent (150%) of the Base Rent")
    for bad in (0.0, -5.0, 5000.0):
        result = {x.name: x for x in validate_fields(lease(holdover_rate_percent=f(bad, "rate of 150%")), [page])}
        assert "invalid_percent" in result["holdover_rate_percent"].flags, bad


def test_a_short_quote_must_match_exactly():
    # "Section 11" is one character away from "Section 16" on the page: close enough to fuzzy-match, so it is rejected
    page = make_page(3, "notice goes to the address set forth in Section 16 of this lease")
    near = {x.name: x for x in validate_fields(lease(notice_section=f("Section 11", "Section 11", 3)), [page])}["notice_section"]
    assert "ungrounded" in near.flags and near.bboxes == []
    exact = {x.name: x for x in validate_fields(lease(notice_section=f("Section 16", "Section 16", 3)), [page])}["notice_section"]
    assert "ungrounded" not in exact.flags and exact.bboxes


@pytest.mark.parametrize(
    "raw,normalized",
    [
        ("Section 11", "Section 11"),
        ("11. Renewal; Termination at Expiration", "Section 11"),
        ("section 4.2", "Section 4.2"),
        ("Article IV", "Article IV"),
    ],
)
def test_notice_section_is_normalized_by_code(raw, normalized):
    page = make_page(3, "11. Renewal; Termination at Expiration and Article IV and Section 4.2 apply")
    quote = {"Article IV": "Article IV", "section 4.2": "Section 4.2"}.get(raw, "11. Renewal; Termination at Expiration")
    result = {x.name: x for x in validate_fields(lease(notice_section=f(raw, quote, 3)), [page])}["notice_section"]
    assert result.value == normalized
