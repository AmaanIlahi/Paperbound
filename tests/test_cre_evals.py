"""The rent roll / T-12 / cross-check evals: scoring, the usable view, reporting and the /evals page. No model is called: the true
column and label mapping of each dev fixture stands in for gpt-4o-mini, so a perfect model must score perfectly, and a wrong
one must show up in the right metric."""

import json
from argparse import Namespace
from datetime import date

import pytest

from paperbound.evals import cre, report, run
from paperbound.evals.budget import Budget
from paperbound import evals_page
from paperbound.pipeline import rent_roll, t12
from paperbound.pipeline.mapping import MappingResult

AS_OF = date(2026, 10, 22)
CURRENT: dict = {}


@pytest.fixture(autouse=True)
def oracle(monkeypatch):
    """The true mapping of whichever fixture is being run replaces the model; `CURRENT['spoil']` can break it on purpose."""
    cre.clear_cache()
    CURRENT.clear()
    original = cre.run_document

    def run_document(folder, as_of, budget=None):
        exp = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        if folder.parent.name == "rent_roll":
            CURRENT["rr"] = exp["mapping"]
        else:
            CURRENT["t12"] = {ln["label"]: ln["category"] for ln in exp["lines"]}
        CURRENT["id"] = exp["id"]
        return original(folder, as_of, budget)

    def map_columns(header, rows, client=None):
        mapping = {int(k): v for k, v in CURRENT["rr"].items()}
        if CURRENT.get("spoil") == "swap_size_and_rent" and CURRENT["id"] == "rr_001":
            mapping[2], mapping[6] = mapping[6], mapping[2]
        return MappingResult(mapping, "gpt-4o-mini", 100, 0.0001)

    def map_labels(labels, client=None):
        cats = dict(CURRENT["t12"])
        if CURRENT.get("spoil") == "utilities_as_taxes":
            cats = {k: ("taxes" if v == "utilities" else v) for k, v in cats.items()}
        return MappingResult({i: cats.get(label) for i, (label, _) in enumerate(labels)}, "gpt-4o-mini", 90, 0.0001)

    monkeypatch.setattr(cre, "run_document", run_document)
    monkeypatch.setattr(rent_roll, "map_rent_roll_columns", map_columns)
    monkeypatch.setattr(t12, "map_t12_labels", map_labels)
    yield
    cre.clear_cache()


def rent_roll_records():
    return [cre.score_rent_roll(cre.run_document(f, AS_OF)) for f in cre.discover("rent_roll")]


def t12_records():
    return [cre.score_t12(cre.run_document(f, AS_OF)) for f in cre.discover("t12")]


def test_a_perfect_mapping_scores_perfectly_on_every_rent_roll_metric():
    g = cre.summarize_rent_roll(rent_roll_records())
    assert g["n_docs"] == 10 and g["n_cells"] > 800 and g["n_columns"] == 4 * 8 + 3 * 7 + 3 * 7 - 1 or g["n_columns"] > 60
    for name in report.RENT_ROLL_RATES:
        assert g[name] == 1.0, name
    assert g["totals_false_alarms"] == 0 and g["totals_planted_found"] == 1.0 and g["usable_view_abstained"] == 0 and g["usable_view_wrong_rate"] == 0.0
    assert set(g["by_layout"]) == {"A", "B", "C", "D"} and all(v["cell_accuracy"] == 1.0 for v in g["by_layout"].values())
    assert all(g[f"metric_{m}"] == 1.0 for m in ("occupancy_units", "total_annual_rent", "walt_rent", "walt_sqft", "expirations_by_year", "expiring_24m"))


def test_swapping_the_size_and_rent_columns_is_caught_by_the_row_arithmetic_and_shows_in_the_usable_view():
    CURRENT["spoil"] = "swap_size_and_rent"
    records = rent_roll_records()
    bad = next(r for r in records if r["id"] == "rr_001")
    assert sum(not m["ok"] for m in bad["mapping"]) == 2 and not bad["flags"]["match"]
    # both columns are numeric, so the "mostly numeric" check cannot see the swap; the row arithmetic (area x rate = rent) does
    flagged_rows = {name: flags for name, flags in bad["flags"]["got"].items() if name.startswith("row_")}
    assert any("row_math_mismatch" in flags for flags in flagged_rows.values())
    assert len(flagged_rows) == bad["rows"]["expected"]  # every row of the document is blocked: the swap leaves nothing usable
    g = cre.summarize_rent_roll(records)
    assert g["header_mapping_accuracy"] < 1.0 and g["cell_accuracy"] < 1.0 and g["header_mapping_exact_docs"] == 0.9
    # the wrong cells sit in rows the app blocks, so what a user is shown is not wrong
    occupied_cells = sum(c["blocked"] for c in bad["cells"])
    assert g["usable_view_abstained"] == occupied_cells > 0 and g["usable_view_wrong_rate"] == 0.0 and g["usable_view_cell_accuracy"] < g["cell_accuracy"]
    assert any(f["kind"] == "header_mapping" for f in cre.failures_rent_roll(records))


def test_a_perfect_labelling_scores_perfectly_on_the_t12_metrics_except_the_abstained_flagged_lines():
    g = cre.summarize_t12(t12_records())
    assert g["n_docs"] == 10
    for name in ("classification_accuracy", "month_headers_correct", "line_count_correct", "line_category_accuracy", "cell_accuracy", "noi_exact_match",
                 "figure_accuracy", "mismatch_detection_recall", "mismatch_detection_precision", "mismatch_detection_exact_docs",
                 "anomaly_recall", "anomaly_precision", "action_correct"):
        assert g[name] == 1.0, name
    assert g["n_mismatch_expected"] == 4 and g["n_mismatch_false_alarms"] == 0  # stated NOI; capex in the opex (total + NOI); one line sum
    # the four lines carrying a planted problem are flagged, so the app blocks them: right category, but abstained
    assert g["usable_view_abstained"] == 4 and g["usable_view_category_accuracy"] < g["line_category_accuracy"] and g["usable_view_wrong_rate"] == 0.0


def test_a_wrong_category_is_a_metric_miss_and_a_failure():
    CURRENT["spoil"] = "utilities_as_taxes"
    records = t12_records()
    g = cre.summarize_t12(records)
    assert g["line_category_accuracy"] < 1.0 and g["noi_exact_match"] == 1.0  # utilities and taxes are both operating expenses: NOI unchanged
    assert any(f["kind"] == "line_category" and f["expected"] == "utilities" for f in cre.failures_t12(records))
    assert g["figure_accuracy"] < 1.0  # but the by-category breakdown is wrong


def test_the_crosscheck_suite_finds_the_planted_mismatch_and_the_other_property():
    records = cre.run_crosscheck(AS_OF)
    g = cre.summarize_crosscheck(records)
    assert g["n_pairs"] == 5 and g["n_negatives"] == 5
    assert g["rent_income_detection"] == 1.0 and g["rent_income_false_alarms"] == 0 and g["property_mismatch_detection"] == 1.0
    assert g["property_same_recognised"] == 1.0 and g["detection_correct"] == 1.0 and g["clean_pairs_passed"] == 1.0 and g["rent_income_difference_ok"] == 1.0
    pair4 = next(r for r in records if r["id"] == "pair_004")
    assert pair4["got_flags"] == ["rent_income_mismatch"] and pair4["rent_income"]["difference_ok"]


def test_the_documents_are_run_once_and_shared_between_suites(monkeypatch):
    calls = []
    original = cre.run_pipeline
    monkeypatch.setattr(cre, "run_pipeline", lambda *a, **k: (calls.append(1), original(*a, **k))[1])
    cre.run_crosscheck(AS_OF)
    assert len(calls) == 10  # five rent rolls and five T-12s, not the negatives' reruns
    cre.run_crosscheck(AS_OF)
    assert len(calls) == 10  # and nothing the second time


def test_run_cre_builds_a_saveable_run_and_the_report_and_page_show_it(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "RESULTS_DIR", tmp_path)
    args = Namespace(limit=None)
    budget = Budget(500, tmp_path / "budget.json")
    runs = [run.run_cre(s, args, budget, AS_OF) for s in ("rent_roll", "t12", "crosscheck")]
    assert [r["suite"] for r in runs] == ["rent_roll", "t12", "crosscheck"] and all(r["aborted"] is None for r in runs)
    assert runs[0]["n_docs"] == 10 and runs[1]["n_docs"] == 10 and runs[2]["n_docs"] == 10 and runs[0]["dataset"] == "dev"
    for r in runs:
        r["failure_log"] = f"failures_{r['suite']}_{r['timestamp']}.json"
        run.save(r)
    latest = report.latest_runs(tmp_path)
    assert set(latest) == {"rent_roll", "t12", "crosscheck"}
    md = report.render_markdown(latest)
    assert "## rent_roll" in md and "Header mapping" in md and "Usable view: cell accuracy" in md and "Planted rent mismatch found" in md
    flat = report.flat_metrics("rent_roll", latest["rent_roll"]["groups"])
    assert flat["rent_roll/all/header_mapping_accuracy"] == 1.0 and flat["rent_roll/all/cell_accuracy"] == 1.0
    assert "t12/all/noi_exact_match" in report.flat_metrics("t12", latest["t12"]["groups"])
    page = evals_page.render(tmp_path)
    assert "Rent roll (dev fixtures)" in page and "T-12 operating statement (dev fixtures)" in page and "Dev only." in page and "Usable view:" in page
    assert report.median_baseline([tmp_path / f"rent_roll_{runs[0]['timestamp']}.json"])["rent_roll/all/cell_accuracy"] == 1.0


def test_a_run_where_every_document_failed_is_not_saved(tmp_path, monkeypatch, capsys):
    """No credits, no network, a bad key: every document errors. Saving that would replace the newest good result with zeros."""
    monkeypatch.setattr(report, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(report, "LATEST_MD", tmp_path / "latest.md")
    monkeypatch.setattr(report, "BASELINE", tmp_path / "baseline.json")
    monkeypatch.setattr(run.config, "OPENAI_API_KEY", "k")
    monkeypatch.setattr(Budget, "install", lambda self: None)
    monkeypatch.setattr(run, "run_cre", lambda suite, *a, **k: {"suite": suite, "records": [{"errors": ["extract: RateLimitError: no credits"]}] * 3})
    status = run.main(["--suite", "rent_roll", "--budget", "5"])
    assert status == 2 and list(tmp_path.glob("*.json")) == [] and not (tmp_path / "latest.md").exists()
    assert "NOT SAVED: every rent_roll document failed" in capsys.readouterr().out
    assert run.failed_everywhere({"suite": "t12", "records": [{"errors": []}, {"errors": ["x"]}]}) is False
    assert run.failed_everywhere({"suite": "cuad", "records": [{"error": "x"}]}) is True and run.failed_everywhere({"suite": "crosscheck", "records": [{"errors": ["x"]}]}) is False


def test_a_new_suite_can_be_merged_into_the_baseline_without_touching_the_old_keys(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"saved": "2026-10-24", "note": "median of three", "metrics": {"generic/generic/all/field_accuracy": 0.93, "rent_roll/all/cell_accuracy": 0.5}}), encoding="utf-8")
    runs = []
    for i, cell in enumerate((0.90, 1.0, 0.98)):
        path = tmp_path / f"rent_roll_2026102{i}_000000.json"
        path.write_text(json.dumps({"suite": "rent_roll", "groups": {"all": {"cell_accuracy": cell, "header_mapping_accuracy": 1.0}}}), encoding="utf-8")
        runs.append(path)
    keys = report.merge_median_baseline(runs, baseline, saved="2026-10-28")
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert keys == ["rent_roll/all/cell_accuracy", "rent_roll/all/header_mapping_accuracy"]
    assert data["metrics"]["generic/generic/all/field_accuracy"] == 0.93  # untouched
    assert data["metrics"]["rent_roll/all/cell_accuracy"] == 0.98  # the median replaced the old value for this suite
    assert "merged later" in data["note"] and data["note"].startswith("median of three") and data["merged"] == {"rent_roll": "2026-10-28"}
