"""Eval harness helpers: normalization, span F1, calibration buckets, ECE, baseline diff, budget cap. No network."""

import json

import pytest

from paperbound.evals import cuad, metrics, report
from paperbound.evals.budget import Budget, BudgetExceeded


# ---- normalization ----

@pytest.mark.parametrize("raw,iso", [
    ("2027-03-31", "2027-03-31"), ("March 31, 2027", "2027-03-31"), ("31 March 2027", "2027-03-31"),
    ("03/31/2027", "2027-03-31"), ("Mar 31, 2027", "2027-03-31"), ("March 31st, 2027", "2027-03-31"),
])
def test_norm_date_accepts_common_formats(raw, iso):
    assert metrics.norm_date(raw) == iso


@pytest.mark.parametrize("raw", [None, "", "three years from the Effective Date", "2027-13-40", "February 30, 2027"])
def test_norm_date_rejects_non_dates(raw):
    assert metrics.norm_date(raw) is None


def test_norm_number_strips_symbols():
    assert metrics.norm_number("$8,500.00") == 8500.0
    assert metrics.norm_number("150%") == 150.0
    assert metrics.norm_number(90) == 90.0
    assert metrics.norm_number("ninety") is None and metrics.norm_number(None) is None and metrics.norm_number(True) is None


def test_field_correct_uses_normalized_exact_for_dates_and_numbers():
    assert metrics.field_correct("end_date", "2027-03-31", "March 31, 2027")
    assert not metrics.field_correct("end_date", "2027-03-31", "2027-03-30")
    assert metrics.field_correct("rent_amount", 8500.0, 8500)
    assert not metrics.field_correct("rent_amount", 8500.0, 8500.5)
    assert metrics.field_correct("policy_2.expiration_date", "2027-01-15", "2027-01-15")
    assert metrics.field_correct("notice_period_days", 90, 90.0)


def test_field_correct_is_fuzzy_for_names_only_above_85():
    assert metrics.field_correct("landlord", "Harborview Properties LLC", "Harborview Properties, LLC")
    assert metrics.field_correct("tenant", "Blue Heron Design Inc.", "Blue Heron Design Inc")
    assert not metrics.field_correct("tenant", "Blue Heron Design Inc.", "Granite Realty Holdings LLC")


def test_field_correct_handles_contains_and_section():
    assert metrics.field_correct("notice_method", ["certified mail"], "Certified Mail, return receipt requested")
    assert not metrics.field_correct("notice_method", ["courier"], "certified mail")
    assert metrics.field_correct("notice_section", "Section 11", "11. Renewal; Termination at Expiration")
    assert not metrics.field_correct("notice_section", "Section 11", "Section 12")


def test_missing_field_is_correct_only_when_null():
    assert metrics.field_correct("security_deposit", None, None)
    assert not metrics.field_correct("security_deposit", None, 5000.0)
    assert not metrics.field_correct("security_deposit", 5000.0, None)


# ---- span token F1 ----

def test_span_prf_perfect_and_disjoint():
    assert metrics.span_prf("the lease ends on May 1, 2027", ["the lease ends on May 1, 2027"]) == (1.0, 1.0, 1.0)
    assert metrics.span_prf("completely different words", ["the lease ends"]) == (0.0, 0.0, 0.0)


def test_span_prf_partial_overlap():
    p, r, f1 = metrics.span_prf("This Agreement ends on May 1, 2027 unless renewed", ["ends on May 1, 2027"])
    assert p == pytest.approx(5 / 9) and r == 1.0 and f1 == pytest.approx(2 * (5 / 9) / (5 / 9 + 1))


def test_span_prf_unions_several_gold_spans_and_ignores_case_and_punctuation():
    p, r, f1 = metrics.span_prf("Acme Corp. and BETA LLC", ["Acme Corp", "Beta LLC"])
    assert (p, r) == (0.8, 1.0)  # "and" is the one token outside the labeled spans


def test_span_prf_handles_empty_prediction_and_empty_gold():
    assert metrics.span_prf(None, ["x"]) == (0.0, 0.0, 0.0)
    assert metrics.span_prf("x", []) == (0.0, 0.0, 0.0)


def test_cuad_dates_in_text_reads_ordinal_day_of_form():
    assert cuad.dates_in_text("the 7th day of September, 1999.") == {"1999-09-07"}
    assert cuad.dates_in_text("effective as of March 15, 2024") == {"2024-03-15"}
    assert cuad.dates_in_text("three years from the Effective Date") == set()


def test_cuad_paginate_numbers_pages_and_keeps_words():
    text = "\n".join(f"line {i} " + "word " * 20 for i in range(100))
    pages = cuad.paginate(text)
    assert len(pages) > 1 and [p.page_no for p in pages] == list(range(1, len(pages) + 1))
    assert sum(len(p.words) for p in pages) == len(text.split())


# ---- calibration ----

@pytest.mark.parametrize("confidence,bucket", [(0.0, "<0.7"), (0.69, "<0.7"), (0.7, "0.7-0.9"), (0.89, "0.7-0.9"), (0.9, ">=0.9"), (1.0, ">=0.9")])
def test_bucket_edges(confidence, bucket):
    assert metrics.bucket_of(confidence) == bucket


def test_calibration_counts_and_accuracy_per_bucket():
    items = [(0.5, False), (0.6, True), (0.8, True), (0.85, False), (0.95, True), (0.99, True), (0.92, True)]
    cal = metrics.calibration(items)["buckets"]
    assert cal["<0.7"]["n"] == 2 and cal["<0.7"]["accuracy"] == 0.5
    assert cal["0.7-0.9"]["n"] == 2 and cal["0.7-0.9"]["accuracy"] == 0.5
    assert cal[">=0.9"]["n"] == 3 and cal[">=0.9"]["accuracy"] == 1.0
    assert cal["<0.7"]["mean_confidence"] == pytest.approx(0.55)


def test_ece_is_weighted_gap_between_confidence_and_accuracy():
    items = [(0.95, True), (0.95, True), (0.95, False), (0.95, True)]  # one bucket: accuracy 0.75, confidence 0.95
    assert metrics.ece(items) == pytest.approx(0.2)
    two = items + [(0.5, False), (0.5, False)]  # second bucket: accuracy 0, confidence 0.5, weight 2/6
    assert metrics.ece(two) == pytest.approx(4 / 6 * 0.2 + 2 / 6 * 0.5)


def test_ece_of_a_perfectly_calibrated_set_is_zero_and_empty_is_none():
    assert metrics.ece([(1.0, True)] * 3) == pytest.approx(0.0)
    assert metrics.ece([]) is None


def test_mean_and_p95():
    assert metrics.mean([1, 2, 3]) == 2 and metrics.mean([]) is None
    assert metrics.p95(list(range(1, 101))) == 95
    assert metrics.p95([5]) == 5 and metrics.p95([]) is None


# ---- baseline diff ----

def test_diff_baseline_flags_drops_over_two_points_only():
    baseline = {"a": 0.90, "b": 0.90, "c": 0.90, "d": 0.50}
    current = {"a": 0.881, "b": 0.879, "c": 0.95, "e": 0.1}  # a: -1.9 points, b: -2.1 points, c: up, d missing, e new
    drops = metrics.diff_baseline(current, baseline)
    assert [d["metric"] for d in drops] == ["b"]
    assert drops[0]["delta_points"] == pytest.approx(-2.1, abs=0.01)


def test_diff_baseline_skips_none_values():
    assert metrics.diff_baseline({"a": None}, {"a": 0.9}) == []
    assert metrics.diff_baseline({"a": 0.1}, {"a": None}) == []


def test_flat_metrics_and_baseline_roundtrip(tmp_path):
    groups = {"lease/all": {"field_accuracy": 0.9, "grounding_rate": None, "n_docs": 20}}
    flat = report.flat_metrics("synthetic", groups)
    assert flat == {"synthetic/lease/all/field_accuracy": 0.9}
    path = tmp_path / "baseline.json"
    report.save_baseline(flat, {"as_of": "2026-10-22"}, path)
    report.save_baseline({"cuad/all/f1": 0.4}, {"as_of": "2026-10-22"}, path)  # a second suite adds to the first
    assert report.load_baseline(path) == {"synthetic/lease/all/field_accuracy": 0.9, "cuad/all/f1": 0.4}
    assert report.load_baseline(tmp_path / "missing.json") == {}


# ---- the call budget ----

def test_budget_refuses_past_the_limit_and_other_models(tmp_path):
    budget = Budget(limit=2, path=tmp_path / "ledger.json")
    budget.charge("gpt-4o-mini")
    budget.charge("gpt-4o-mini")
    with pytest.raises(BudgetExceeded):
        budget.charge("gpt-4o-mini")
    with pytest.raises(BudgetExceeded):
        Budget(limit=5, path=tmp_path / "other.json").charge("gpt-4o")
    assert json.loads((tmp_path / "ledger.json").read_text())["calls"] == 2


def test_the_limit_is_per_run_and_the_ledger_only_reports_the_total(tmp_path):
    ledger = tmp_path / "ledger.json"
    first = Budget(limit=2, path=ledger)
    first.charge("gpt-4o-mini"), first.charge("gpt-4o-mini")
    second = Budget(limit=2, path=ledger)  # a new run starts with its whole allowance, however much the ledger holds
    assert second.calls == 2 and second.session_calls == 0 and second.remaining == 2
    second.charge("gpt-4o-mini")
    assert second.remaining == 1 and second.calls == 3
    saved = json.loads(ledger.read_text())
    assert saved["calls"] == 3 and saved["last_run_calls"] == 1 and saved["last_run_limit"] == 2
    ledger.write_text(json.dumps({"calls": 10_000}))  # a huge old total refuses nothing
    assert Budget(limit=1, path=ledger).remaining == 1


# ---- the /evals page reads saved runs and needs no database ----

def _record(doc_type="lease", variant="clean", injected=False):
    field = {"key": "rent_amount", "base": "rent_amount", "expected": 1.0, "extracted": 1.0, "match": True, "expect_present": True,
             "blocked": False, "usable": True, "confidence": 0.95, "grounded": True, "page": 1, "page_ok": True, "flags": [], "quote": "$1.00"}
    inj = {"model_resisted": True, "held": True, "flagged": False, "unflagged_breach": False, "reached_actions": False} if injected else None
    return {"id": "x", "doc_type": doc_type, "variant": variant, "classification": {"match": True}, "fields": [field],
            "obligation": {"match": True}, "brief": {"lines": [{"status": "match"}], "unexpected": [], "match": True},
            "actions": {"match": True, "unsafe": False}, "injection": inj, "cost_usd": 0.001, "tokens": 100, "latency_ms": 2000, "calls": 2}


def test_evals_page_renders_saved_suite_runs_without_a_database(tmp_path, monkeypatch):
    from paperbound import evals_page

    groups = report.summarize_synthetic([_record(), _record(variant="injection", injected=True)])
    run = {"suite": "synthetic", "timestamp": "20261022_120000", "as_of": "2026-10-22", "n_docs": 2, "groups": groups,
           "failure_modes": [{"mode": "wrong_field: <b>x</b>", "count": 3, "examples": []}], "failure_log": "f.json"}
    (tmp_path / "synthetic_20261022_120000.json").write_text(json.dumps(run), encoding="utf-8")
    (tmp_path / "failures_synthetic_20261022_120000.json").write_text("[]", encoding="utf-8")  # a list, not a summary
    monkeypatch.setattr(report, "RESULTS_DIR", tmp_path)
    html = evals_page.render(tmp_path)
    assert "Template-generated" in html and "Prompt injection" in html and "Calibration" in html
    assert "wrong_field: &lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html
    assert "Real contracts" not in html  # no CUAD run saved
    assert "No saved results yet" in html  # the demo-fixture table still has its empty state


def test_group_metrics_separates_wrong_from_withheld_brief_lines():
    rec = _record()
    rec["brief"] = {"lines": [{"status": "match"}, {"status": "withheld"}, {"status": "wrong"}], "unexpected": [], "match": False}
    g = report.group_metrics([rec])
    assert g["brief_line_correct"] == pytest.approx(1 / 3) and g["brief_line_wrong_rate"] == pytest.approx(1 / 3)


# ---- the usable view: a field with a blocking flag counts as abstained ----

def test_usable_view_counts_a_blocked_field_as_not_extracted():
    from paperbound.evals.report import usable_view

    def row(expect_present, extracted, match, blocked):
        return {"expect_present": expect_present, "extracted": extracted, "match": match, "blocked": blocked}

    rows = [
        row(True, 1, True, False),      # right and usable
        row(True, 2, False, False),     # wrong and shown: the error that reaches the user
        row(True, 3, True, True),       # right but blocked: abstained, so a miss in this view
        row(False, 150.0, False, True),  # invented but blocked: abstained, so correct in this view (wrong in field_accuracy)
        row(False, 9.0, False, False),  # invented and not blocked: wrong
        row(False, None, True, False),  # nothing there, nothing extracted
    ]
    view = usable_view(rows)
    assert view["usable_view_accuracy"] == pytest.approx(3 / 6)  # rows 1, 4 and 6
    assert view["usable_view_missing_correct"] == pytest.approx(2 / 3)
    assert view["usable_view_wrong_rate"] == pytest.approx(2 / 3)  # unblocked answers are rows 1, 2 and 5; two are wrong
    assert view["usable_view_abstained"] == 2
