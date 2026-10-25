"""Amounts are parsed by code from the grounded quote (the model's number is only a cross-check); dates accept the
common written forms. Both are shared by the typed and the generic paths."""

import pytest

from paperbound.evals.budget import Budget, BudgetExceeded
from paperbound.pipeline.validate import _dates_in, amounts_in, quote_rule_ok, reconcile_amount, validate_generic, value_in_quote
from paperbound.schemas.generic import FallbackAmount
from tests.generic_fakes import fallback, notice_pages


@pytest.mark.parametrize("text, expected", [
    ("$250,000.00 USD", [250000.0]),
    ("Capital call amount: $250,000.00 USD", [250000.0]),
    ("USD 8500", [8500.0]),
    ("EUR 45.00", [45.0]),
    ("Total due: 4,812.50 USD.", [4812.5]),
    ("EACH OCCURRENCE $1,000,000", [1000000.0]),
    ("initial rate of Eight Thousand Five Hundred Dollars ($8,500.00) per month", [8500.0]),  # restated in brackets: positive
    ("Credit: ($1,200.00)", [-1200.0]),
    ("Balance (1,200.00)", [-1200.0]),
    ("-$50.25", [-50.25]),
    ("Fee $437.50 on top of $4,375.00", [437.5, 4375.0]),
    ("Rent: 8,500.00", [8500.0]),  # no sign: every figure counts
    ("ninety (90) days", [90.0]),  # brackets around a plain number are not accounting negatives
    ("no figures here", []),
])
def test_amounts_are_parsed_from_the_quote_by_code(text, expected):
    assert amounts_in(text) == expected


def test_only_marked_figures_count_when_there_are_any():
    assert amounts_in("Invoice 20261005 total $4,812.50") == [4812.5]


def test_the_quote_decides_and_the_model_is_a_cross_check():
    assert reconcile_amount(250.0, "Capital call amount: $250,000.00 USD") == (250000.0, True)
    assert reconcile_amount(250000.0, "Capital call amount: $250,000.00 USD") == (250000.0, False)
    # several amounts: the model picks, and its pick must be one of them (value_in_quote judges that)
    assert reconcile_amount(437.5, "Fee $437.50 on top of $4,375.00") == (437.5, False)
    assert reconcile_amount(99.0, "Fee $437.50 on top of $4,375.00") == (99.0, False)
    assert not value_in_quote("amount", 99.0, "Fee $437.50 on top of $4,375.00")
    assert reconcile_amount(5.0, "no figures") == (5.0, False)


def test_a_generic_amount_the_model_misreads_is_corrected_and_blocked():
    wrong = fallback(parties=[], dates=[], obligations=[], amounts=[
        FallbackAmount(value=250.0, currency="USD", label="capital call amount", source_quote="Capital call amount: $250,000.00 USD", page=1)])
    field = next(f for f in validate_generic(wrong, notice_pages()) if f.name == "amount_1.amount")
    assert field.value == 250000.0 and "amount_mismatch" in field.flags and not field.validators_passed
    right = fallback(parties=[], dates=[], obligations=[], amounts=[
        FallbackAmount(value=250000.0, currency="USD", label="capital call amount", source_quote="Capital call amount: $250,000.00 USD", page=1)])
    assert next(f for f in validate_generic(right, notice_pages()) if f.name == "amount_1.amount").flags == []


def test_a_negative_amount_is_invalid_for_a_field_that_must_be_positive():
    neg = fallback(parties=[], dates=[], obligations=[], amounts=[
        FallbackAmount(value=-1200.0, currency="USD", label="credit", source_quote="Capital call amount: $250,000.00 USD", page=1)])
    assert "amount_mismatch" in next(f for f in validate_generic(neg, notice_pages()) if f.name == "amount_1.amount").flags


@pytest.mark.parametrize("text", [
    "Notice date: November 15, 2026", "Nov. 15, 2026", "Nov 15, 2026", "Nov15, 2026", "15 Nov 2026", "15 Nov. 2026",
    "15th November 2026", "11/15/2026", "2026-11-15", "2026/11/15", "NOV 15 2026",
])
def test_dates_are_accepted_in_common_written_forms(text):
    assert _dates_in(text) == {"2026-11-15"}
    assert quote_rule_ok("date", text) and value_in_quote("date", "2026-11-15", text)
    assert quote_rule_ok("expiration_date", text) and value_in_quote("expiration_date", "2026-11-15", text)


def test_september_abbreviations_and_invalid_dates():
    assert _dates_in("Sept 3, 2026 and Sep. 4, 2026") == {"2026-09-03", "2026-09-04"}
    assert _dates_in("Feb 30, 2026 and 13/45/2026") == set()
    assert _dates_in("The 15 days after Nov were fine") == set()


def test_the_eval_budget_refuses_any_call_that_is_not_temperature_zero(tmp_path):
    budget = Budget(limit=5, path=tmp_path / "b.json")
    budget.charge("gpt-4o-mini", 0)
    with pytest.raises(BudgetExceeded, match="temperature"):
        budget.charge("gpt-4o-mini", 0.7)
    with pytest.raises(BudgetExceeded, match="temperature"):
        budget.charge("gpt-4o-mini", None)  # an unset temperature is the API default, not 0
    from paperbound.evals import budget as module
    assert module.DEFAULT_LIMIT == 500


def test_the_baseline_is_the_median_of_the_saved_runs(tmp_path):
    import json

    from paperbound.evals import report

    files = []
    for i, value in enumerate((0.5, 0.9, 0.7)):
        path = tmp_path / f"cuad_{i}.json"
        path.write_text(json.dumps({"suite": "cuad", "groups": {"all": {"f1": value, "precision": 0.1 * (i + 1)}}}), encoding="utf-8")
        files.append(path)
    assert report.median_baseline(files) == {"cuad/all/f1": 0.7, "cuad/all/precision": 0.2}


@pytest.mark.parametrize("text", [
    "March 03,2020", "March 3 , 2020", "March  3,  2020", "Mar. 03 , 2020", "03 March , 2020", "3rd of March 2020", "3 March,2020",
])
def test_dates_tolerate_missing_and_extra_whitespace_around_commas_and_periods(text):
    assert _dates_in(text) == {"2020-03-03"}


def test_whitespace_around_the_period_of_an_abbreviation_and_in_numeric_dates():
    assert _dates_in("Nov.15,2026") == _dates_in("Nov . 15 , 2026") == _dates_in("11 / 15 / 2026") == {"2026-11-15"}
    assert _dates_in("March 2020") == set() and _dates_in("March 3") == set()


def test_nonstandard_grouping_parses_without_a_false_flag():
    assert amounts_in("$1000,000.00") == [1000000.0] and amounts_in("rent of 1000,000") == [1000000.0]
    q = "Capital call amount: $1000,000.00 USD"
    wrong = fallback(parties=[], dates=[], obligations=[], amounts=[FallbackAmount(value=1000000.0, currency="USD", label="call", source_quote=q, page=1)])
    from paperbound.pipeline.parse import ParsedPage
    page = ParsedPage(page_no=1, route="text_layer", text=q, words=[
        {"text": w, "bbox": [i * 10, 0, i * 10 + 9, 10]} for i, w in enumerate(q.split())], quality_score=1.0, width_pt=600, height_pt=800, image_png=b"")
    field = next(f for f in validate_generic(wrong, [page]) if f.name == "amount_1.amount")
    assert field.value == 1000000.0 and field.flags == [] and field.validators_passed


@pytest.mark.parametrize("text", [
    "19 day of March, 2004", "19th day of March, 2004", "the 19th day of March 2004", "this 19th  day  of  March ,2004",
    "entered into as of the 19 day of Mar. 2004", "On the 19th day of March, 2004, the parties agree",
])
def test_day_of_month_forms_are_dates(text):
    assert _dates_in(text) == {"2004-03-19"}
    assert quote_rule_ok("date", text) and value_in_quote("date", "2004-03-19", text)


def test_day_of_forms_do_not_invent_dates():
    assert _dates_in("the 19th day of March") == set()  # no year
    assert _dates_in("the 31st day of April, 2004") == set()  # no such day
    assert _dates_in("60 days of March 2004 notice") == set()  # "days", not "day of"
