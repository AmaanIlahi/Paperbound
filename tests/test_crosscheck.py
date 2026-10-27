"""Rent roll <-> T-12 cross-check: property identity, rent income against the tolerance, a vacancy sanity warning. Code only."""

import json
from datetime import date
from pathlib import Path

import pytest

from paperbound.pipeline import crosscheck, property as prop
from paperbound.pipeline import rent_roll as rr
from paperbound.pipeline import t12
from paperbound.pipeline.mapping import MappingResult
from paperbound.pipeline.parse import parse_pdf
from tests.cre_fakes import PAGES, standard_rows, t12_validated, validated
from tests.lease_fakes import TODAY
from tests.live import live  # noqa: F401

FIXTURES = Path(__file__).resolve().parent.parent / "evals" / "fixtures"


def load_pair(n: int):
    """(rent roll fields, T-12 fields, pair truth) for a dev pair, read through the real table code with the true column and label mapping."""
    truth = json.loads((FIXTURES / "pairs" / f"pair_{n:03d}" / "expected.json").read_text(encoding="utf-8"))
    rr_dir, t_dir = FIXTURES / "rent_roll" / truth["rent_roll"], FIXTURES / "t12" / truth["t12"]
    rr_exp, t_exp = (json.loads((d / "expected.json").read_text(encoding="utf-8")) for d in (rr_dir, t_dir))
    rr_pdf, t_pdf = (rr_dir / "doc.pdf").read_bytes(), (t_dir / "doc.pdf").read_bytes()
    rr_ext = rr.extract_rent_roll(None, rr_pdf, mapper=lambda h, r: MappingResult({int(k): v for k, v in rr_exp["mapping"].items()}, "m", 0, 0.0)).fields
    want = {ln["label"]: ln["category"] for ln in t_exp["lines"]}
    t_ext = t12.extract_t12(None, t_pdf, mapper=lambda labels: MappingResult({i: want.get(label) for i, (label, _) in enumerate(labels)}, "m", 0, 0.0)).fields
    return rr.validate_rent_roll(rr_ext, parse_pdf(rr_pdf), TODAY), t12.validate_t12(t_ext, parse_pdf(t_pdf), TODAY), truth


def check(n, **kw):
    a, b, truth = load_pair(n)
    return crosscheck.cross_check(1, 2, a, b, today=TODAY, **kw), truth


def by_key(report):
    return {c["key"]: c for c in report["comparisons"]}


# ---- property identity ----

def test_the_property_is_read_from_the_first_lines_of_page_one():
    pages = parse_pdf((FIXTURES / "t12" / "t12_003" / "doc.pdf").read_bytes())
    assert prop.find_property(pages) == {"name": "Mill Creek Center", "address": "88 Mill Creek Parkway, Raleigh, NC 27601",
                                         "lines": ["Mill Creek Center", "88 Mill Creek Parkway, Raleigh, NC 27601"]}
    field = prop.property_field(pages)
    assert field.name == "property" and field.page_no == 1 and field.bbox and field.flags == []
    assert prop.find_property([]) is None and prop.find_property(parse_pdf((FIXTURES / "lease" / "lease_001" / "doc.pdf").read_bytes())) is not None


def test_names_and_addresses_match_through_spelling_and_abbreviation_differences():
    assert prop.names_match("Harborview Commons", "HARBORVIEW COMMONS LLC")[0]
    assert prop.names_match("The Westgate Office Park", "Westgate Office Park")[0]
    assert not prop.names_match("Harborview Commons", "Cedar Point Plaza")[0]
    assert prop.addresses_match("1200 Harbor Street, Baltimore, MD 21202", "1200 Harbor St., Baltimore MD 21202")[0]
    assert not prop.addresses_match("1200 Harbor Street, Baltimore, MD 21202", "455 Cedar Point Road, Columbus, OH 43215")[0]


# ---- the dev pairs ----

@pytest.mark.parametrize("n", [1, 2, 3, 5])
def test_a_matching_pair_has_nothing_to_review(n):
    report, truth = check(n)
    c = by_key(report)
    assert report["status"] == "ok" and report["flags"] == [], c["rent_income"]["text"]
    assert c["property_identity"]["status"] == "ok" and c["rent_income"]["status"] == "ok" and c["vacancy_sanity"]["status"] == "ok"
    assert c["rent_income"]["rent_roll"]["value"] == pytest.approx(truth["rent_roll_total_annual_rent"], abs=0.01)
    assert c["rent_income"]["t12"]["value"] == pytest.approx(truth["t12_base_rent"], abs=0.01)
    assert c["rent_income"]["difference"] == pytest.approx(truth["difference"], abs=0.01)
    assert abs(c["rent_income"]["difference_share"]) <= 0.05


def test_the_planted_rent_income_mismatch_is_found_and_everything_else_agrees():
    report, truth = check(4)
    c = by_key(report)
    assert truth["expected_flags"] == ["rent_income_mismatch"]
    assert report["status"] == "review" and report["flags"] == [{"flag": "rent_income_mismatch", "severity": "mismatch", "key": "rent_income"}]
    assert c["rent_income"]["status"] == "mismatch" and c["rent_income"]["difference_share"] == pytest.approx(truth["difference_share"], abs=0.0001)
    assert c["rent_income"]["difference"] < 0 and "outside the 5.0% tolerance" in c["rent_income"]["text"]
    assert c["property_identity"]["status"] == "ok"


def test_each_comparison_carries_both_values_their_sources_and_the_difference():
    c = by_key(check(1)[0])["rent_income"]
    assert c["rent_roll"]["label"] == "Rent roll total annual rent" and c["rent_roll"]["sources"] and all(s.startswith("row_") for s in c["rent_roll"]["sources"])
    assert c["t12"]["label"] == "T-12 base rent revenue" and c["t12"]["sources"] and all(s.startswith("line_") for s in c["t12"]["sources"])
    assert {"difference", "difference_share", "tolerance", "text"} <= set(c)


def test_the_tolerance_is_configurable():
    loose, _ = check(4, tolerance=0.25)
    assert by_key(loose)["rent_income"]["status"] == "ok"  # 18% off is inside 25%
    strict_report, truth = check(1, tolerance=abs(check(1)[0]["comparisons"][1]["difference_share"]) / 2)
    assert by_key(strict_report)["rent_income"]["flag"] == "rent_income_mismatch"


def test_two_different_properties_raise_a_property_mismatch_warning():
    a, _, _ = load_pair(1)
    _, b, _ = load_pair(2)
    report = crosscheck.cross_check(1, 2, a, b, today=TODAY)
    c = by_key(report)["property_identity"]
    assert c["status"] == "warning" and c["flag"] == "property_mismatch" and report["status"] == "review"
    assert {f["flag"]: f["severity"] for f in report["flags"]}["property_mismatch"] == "warning"


def test_a_document_without_a_readable_property_cannot_be_compared_on_identity():
    a, b, _ = load_pair(1)
    report = crosscheck.cross_check(1, 2, [f for f in a if f.name != "property"], b, today=TODAY)
    c = by_key(report)["property_identity"]
    assert c["status"] == "unavailable" and "the rent roll" in c["text"] and c["flag"] is None


# ---- availability and the vacancy sanity check ----

def test_unusable_sides_are_reported_as_unavailable_not_guessed():
    flagged_rent_roll = validated([["1", "Acme", "1,000", "01/01/2024", "12/31/2028", "$5,000.00", "$60,000.00", "$99.00"]])  # row math mismatch
    report = crosscheck.cross_check(1, 2, flagged_rent_roll, t12_validated(), today=TODAY)
    c = by_key(report)["rent_income"]
    assert c["status"] == "unavailable" and "no usable total annual rent" in c["text"] and report["status"] == "incomplete"
    rows = standard_rows()
    categories = {"Rent": None}
    no_base = t12_validated(rows, {**__import__("tests.cre_fakes", fromlist=["CATEGORIES"]).CATEGORIES, **categories})
    assert "no usable base rent line" in by_key(crosscheck.cross_check(1, 2, validated(), no_base, today=TODAY))["rent_income"]["text"]


def test_a_t12_that_loses_far_more_than_the_rent_roll_vacancy_is_a_warning_only():
    # rent roll 100% occupied; T-12 loses 4% of gross income: fine. Raise the loss to 25% and it warns.
    full = validated([["1", "Acme", "1,000", "01/01/2024", "12/31/2028", "$5,000.00", "$60,000.00", "$60.00"]])
    ok = by_key(crosscheck.cross_check(1, 2, full, t12_validated(), today=TODAY))["vacancy_sanity"]
    assert ok["status"] == "ok" and ok["flag"] is None
    heavy = t12_validated(standard_rows(Vacancy=-40000))
    warn = by_key(crosscheck.cross_check(1, 2, full, heavy, today=TODAY))["vacancy_sanity"]
    assert warn["status"] == "warning" and warn["flag"] == "vacancy_sanity" and "more than the rent roll" in warn["text"]
    report = crosscheck.cross_check(1, 2, full, heavy, today=TODAY)
    assert {f["flag"] for f in report["flags"]} >= {"vacancy_sanity"} and all(f["severity"] == "warning" for f in report["flags"] if f["flag"] == "vacancy_sanity")


def test_a_rent_roll_with_a_totals_problem_says_which_figure_it_used():
    a, b, _ = load_pair(3)  # rr_003 plants a totals mismatch
    c = by_key(crosscheck.cross_check(1, 2, a, b, today=TODAY))["rent_income"]
    assert c["status"] == "ok" and "do not add up to its stated totals" in c["rent_roll"]["note"]


# ---- the API ----

@pytest.fixture()
def pair_env(live, monkeypatch):  # noqa: F811
    """Uploads go through the real classifier (the fixtures classify by heuristics) and the real table code; only the two
    mapping calls are replaced, by the true mapping of whichever dev fixture was uploaded last."""
    state = {}
    monkeypatch.setattr(rr, "map_rent_roll_columns", lambda header, rows, client=None: MappingResult(state["rr"], "gpt-4o-mini", 100, 0.0001))
    monkeypatch.setattr(t12, "map_t12_labels", lambda labels, client=None: MappingResult(
        {i: state["t12"].get(label) for i, (label, _) in enumerate(labels)}, "gpt-4o-mini", 90, 0.0001))
    live.use_pipeline(lambda: {"today": TODAY})

    def upload_pair(n: int, client=None):
        truth = json.loads((FIXTURES / "pairs" / f"pair_{n:03d}" / "expected.json").read_text(encoding="utf-8"))
        ids = {}
        for key, folder, kind in (("rr", truth["rent_roll"], "rent_roll"), ("t12", truth["t12"], "t12")):
            exp = json.loads((FIXTURES / kind / folder / "expected.json").read_text(encoding="utf-8"))
            state[key] = {int(k): v for k, v in exp["mapping"].items()} if key == "rr" else {ln["label"]: ln["category"] for ln in exp["lines"]}
            ids[key] = live.upload_and_run((FIXTURES / kind / folder / "doc.pdf").read_bytes(), f"{kind}.pdf", client)["run_id"]
        return ids, truth

    live.upload_pair = upload_pair
    return live


def test_post_crosscheck_returns_the_report(pair_env):
    ids, truth = pair_env.upload_pair(4)
    assert pair_env.client.get(f"/runs/{ids['rr']}").json()["doc_type"] == "rent_roll" and pair_env.client.get(f"/runs/{ids['t12']}").json()["doc_type"] == "t12"
    response = pair_env.client.post("/crosscheck", json={"rent_roll_run_id": ids["rr"], "t12_run_id": ids["t12"]})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "review" and [f["flag"] for f in report["flags"]] == ["rent_income_mismatch"] and report["tolerance"] == 0.05
    rent = {c["key"]: c for c in report["comparisons"]}["rent_income"]
    assert rent["rent_roll"]["run_id"] == ids["rr"] and rent["t12"]["run_id"] == ids["t12"]
    assert rent["difference_share"] == pytest.approx(truth["difference_share"], abs=0.0001)
    first = rent["rent_roll"]["sources"][0]
    assert first["name"].startswith("row_") and first["page_no"] == 1 and first["bboxes"] and first["field_id"]
    assert rent["t12"]["sources"][0]["name"].startswith("line_")
    assert report["documents"] == {"rent_roll": {"run_id": ids["rr"], "filename": "rent_roll.pdf"}, "t12": {"run_id": ids["t12"], "filename": "t12.pdf"}}
    ok = pair_env.client.post("/crosscheck", json={"rent_roll_run_id": ids["rr"], "t12_run_id": ids["t12"], "tolerance": 0.25}).json()
    assert ok["status"] == "ok" and ok["tolerance"] == 0.25


def test_a_matching_pair_is_ok_through_the_api(pair_env):
    ids, _ = pair_env.upload_pair(1)
    report = pair_env.client.post("/crosscheck", json={"rent_roll_run_id": ids["rr"], "t12_run_id": ids["t12"]}).json()
    assert report["status"] == "ok" and report["flags"] == []


def test_the_two_runs_must_be_the_right_types_and_completed(pair_env):
    ids, _ = pair_env.upload_pair(1)
    post = lambda a, b, **kw: pair_env.client.post("/crosscheck", json={"rent_roll_run_id": a, "t12_run_id": b, **kw})  # noqa: E731
    swapped = post(ids["t12"], ids["rr"])
    assert swapped.status_code == 422 and "is a t12, not a rent roll" in swapped.json()["detail"]
    assert post(ids["rr"], ids["rr"]).status_code == 422
    assert post(ids["rr"], ids["t12"], tolerance=0).status_code == 422 and post(ids["rr"], ids["t12"], tolerance=2).status_code == 422
    queued = pair_env.upload((FIXTURES / "t12" / "t12_010" / "doc.pdf").read_bytes(), "t12.pdf")  # uploaded, never worked
    assert queued.status_code == 200
    waiting = post(ids["rr"], queued.json()["run_id"])
    assert waiting.status_code == 422 and "not classified" in waiting.json()["detail"]
    lease = pair_env.upload_and_run()["run_id"]
    assert "lease" in post(lease, ids["t12"]).json()["detail"]
    from paperbound.db.models import Run
    with pair_env.Session() as s:
        s.get(Run, ids["t12"]).status = "partial"
        s.commit()
    assert post(ids["rr"], ids["t12"]).status_code == 409


def test_only_the_owner_can_compare_their_runs(pair_env):
    ids, _ = pair_env.upload_pair(1)
    other = pair_env.visitor()
    assert other.post("/crosscheck", json={"rent_roll_run_id": ids["rr"], "t12_run_id": ids["t12"]}).status_code == 404
    assert pair_env.client.post("/crosscheck", json={"rent_roll_run_id": 9999, "t12_run_id": ids["t12"]}).status_code == 404
    assert pair_env.client.post("/crosscheck", json={"rent_roll_run_id": ids["rr"]}).status_code == 422


def test_the_run_list_is_the_callers_own_and_can_be_filtered(pair_env):
    ids, _ = pair_env.upload_pair(2)
    mine = pair_env.client.get("/runs").json()
    assert {r["id"] for r in mine} == {ids["rr"], ids["t12"]} and mine[0]["id"] == max(ids.values())
    only = pair_env.client.get("/runs", params={"doc_type": "t12"}).json()
    assert [r["id"] for r in only] == [ids["t12"]] and only[0]["status"] == "done" and only[0]["filename"] == "t12.pdf"
    assert pair_env.visitor().get("/runs").json() == []


def test_the_compare_view_shows_the_report(pair_env, tmp_path):
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    ids, _ = pair_env.upload_pair(4)
    report = pair_env.client.post("/crosscheck", json={"rent_roll_run_id": ids["rr"], "t12_run_id": ids["t12"]}).json()
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    done = subprocess.run(["node", str(Path(__file__).parent / "ui" / "compare.test.js"), str(path)], capture_output=True, text=True, timeout=90)
    assert done.returncode == 0, done.stdout + done.stderr
