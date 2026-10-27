"""The held-out scaffolding: split_pdf, the label validator and check_labels, and the --heldout eval flag.

Everything runs on temporary folders built from the dev fixtures. Nothing here opens evals/heldout (which is where the real
documents and labels go), and no model is called: the true mapping stands in for it.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from paperbound.evals import cre, labels, report, run
from tests.cre_fakes import install_oracle

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "evals" / "fixtures"
TEMPLATES = ROOT / "evals" / "label_templates"
SCRIPTS = ROOT / "scripts"


# ---------------------------------------------------------------------------------------------------------------
# split_pdf
# ---------------------------------------------------------------------------------------------------------------

def make_pdf(path: Path, pages: int = 6) -> Path:
    doc = pymupdf.open()
    for i in range(1, pages + 1):
        doc.new_page().insert_text((72, 72), f"page marker {i}")
    doc.save(path)
    doc.close()
    return path


def texts(path: Path) -> list[str]:
    with pymupdf.open(path) as d:
        return [p.get_text().strip() for p in d]


@pytest.fixture()
def split():
    sys.path.insert(0, str(SCRIPTS))
    import split_pdf

    yield split_pdf
    sys.path.remove(str(SCRIPTS))


def test_a_page_range_is_copied_into_a_new_pdf(split, tmp_path):
    source = make_pdf(tmp_path / "om.pdf")
    before = source.read_bytes()
    out = tmp_path / "out" / "rent_roll" / "prop_a.pdf"  # the folder is created
    assert split.split_pdf(source, "2-4", out) == 3
    assert texts(out) == ["page marker 2", "page marker 3", "page marker 4"]
    assert source.read_bytes() == before, "the input is not changed"


def test_page_lists_keep_their_order_and_mix_with_ranges(split, tmp_path):
    source = make_pdf(tmp_path / "om.pdf")
    assert split.split_pdf(source, "5, 1,2-3", tmp_path / "a.pdf") == 4
    assert texts(tmp_path / "a.pdf") == ["page marker 5", "page marker 1", "page marker 2", "page marker 3"]
    assert split.split_pdf(source, "6", tmp_path / "b.pdf") == 1 and texts(tmp_path / "b.pdf") == ["page marker 6"]


@pytest.mark.parametrize("spec, message", [
    ("0", "page 0 does not exist"), ("7", "page 7 does not exist: the PDF has 6 page(s)"), ("3-9", "page 7 does not exist"),
    ("4-2", "runs backwards"), ("", "no pages given"), ("a-b", "not a page range"), ("1,,2", "empty item"), ("two", "not a page number"),
])
def test_bad_page_specs_say_what_is_wrong(split, tmp_path, spec, message):
    source = make_pdf(tmp_path / "om.pdf")
    with pytest.raises(ValueError, match=re.escape(message)):
        split.split_pdf(source, spec, tmp_path / "x.pdf")
    assert not (tmp_path / "x.pdf").exists(), "nothing is written for a bad request"


def test_an_existing_output_is_not_overwritten_without_force_and_never_the_input(split, tmp_path):
    source = make_pdf(tmp_path / "om.pdf")
    target = tmp_path / "x.pdf"
    split.split_pdf(source, "1", target)
    with pytest.raises(ValueError, match="already exists"):
        split.split_pdf(source, "2", target)
    assert texts(target) == ["page marker 1"]
    split.split_pdf(source, "2", target, force=True)
    assert texts(target) == ["page marker 2"]
    with pytest.raises(ValueError, match="different file"):
        split.split_pdf(source, "1", source, force=True)
    with pytest.raises(ValueError, match="does not exist"):
        split.split_pdf(tmp_path / "missing.pdf", "1", tmp_path / "y.pdf")
    (tmp_path / "not.pdf").write_text("this is not a pdf")
    with pytest.raises(ValueError, match="cannot open"):
        split.split_pdf(tmp_path / "not.pdf", "1", tmp_path / "z.pdf")


def test_the_command_line_matches_the_documented_example(tmp_path):
    source = make_pdf(tmp_path / "om.pdf", 20)
    out = tmp_path / "evals" / "heldout" / "rent_roll" / "prop_a.pdf"
    done = subprocess.run([sys.executable, str(SCRIPTS / "split_pdf.py"), str(source), "--pages", "14-16", "-o", str(out)], capture_output=True, text=True)
    assert done.returncode == 0 and "wrote 3 page(s)" in done.stdout and texts(out)[0] == "page marker 14"
    bad = subprocess.run([sys.executable, str(SCRIPTS / "split_pdf.py"), str(source), "--pages", "30", "-o", str(tmp_path / "q.pdf")], capture_output=True, text=True)
    assert bad.returncode == 1 and "error: page 30 does not exist" in bad.stderr


# ---------------------------------------------------------------------------------------------------------------
# labels and check_labels
# ---------------------------------------------------------------------------------------------------------------

def heldout_tree(tmp_path: Path, rent_rolls=("rr_001", "rr_003", "rr_004"), t12s=("t12_001", "t12_003", "t12_006"), pairs=("pair_001", "pair_003")) -> Path:
    """A held-out folder (named heldout, flat layout) made from dev fixtures: what a person's own files would look like."""
    root = tmp_path / "heldout"
    for kind, names, prefix in (("rent_roll", rent_rolls, "rent"), ("t12", t12s, "t12")):
        (root / kind).mkdir(parents=True)
        for name in names:
            stem = f"{prefix}_{name.split('_')[1]}"
            shutil.copy(FIXTURES / kind / name / "doc.pdf", root / kind / f"{stem}.pdf")
            label = json.loads((FIXTURES / kind / name / "expected.json").read_text(encoding="utf-8"))
            label.update(id=stem, pdf=f"{stem}.pdf")
            label.pop("dev_only", None)
            (root / kind / f"{stem}.json").write_text(json.dumps(label), encoding="utf-8")
    (root / "pairs").mkdir()
    for name in pairs:
        label = json.loads((FIXTURES / "pairs" / name / "expected.json").read_text(encoding="utf-8"))
        n = name.split("_")[1]
        label.update(id=f"pair_{n}", rent_roll=f"rent_{n}", t12=f"t12_{n}")
        (root / "pairs" / f"pair_{n}.json").write_text(json.dumps(label), encoding="utf-8")
    return root


def test_labels_made_from_the_dev_fixtures_are_complete(tmp_path):
    root = heldout_tree(tmp_path)
    found = labels.find_labels(root)
    assert len(found) == 8
    for kind, path in found:
        result = labels.validate(path)
        assert result.ok, (path.name, result.errors)


@pytest.mark.parametrize("name, kind, folder", [("rent_roll", "rent_roll", "rent_roll"), ("t12", "t12", "t12"), ("pair", "pairs", "pairs")])
def test_a_blank_template_reports_what_is_missing(tmp_path, name, kind, folder):
    root = tmp_path / "heldout"
    (root / folder).mkdir(parents=True)
    path = root / folder / "prop_a.json"
    shutil.copy(TEMPLATES / f"{name}.template.json", path)
    result = labels.validate(path)
    assert not result.ok
    text = " | ".join(result.errors)
    assert "id: is empty" in text and "as_of: is empty" in text
    expected = {"rent_roll": ["headers: is empty", "mapping: is empty", "rows: is empty", "pdf: is empty"],
                "t12": ["months: is empty", "lines: is empty", "pdf: is empty"],
                "pair": ["rent_roll: is empty", "t12: is empty", "rent_roll_total_annual_rent: must be a number", "difference_share: must be a number"]}[name]
    for piece in expected:
        assert piece in text, (piece, result.errors)
    assert json.loads((TEMPLATES / f"{name}.template.json").read_text(encoding="utf-8"))  # and the template itself is valid JSON


def test_the_templates_have_a_comment_on_every_field():
    for name in ("rent_roll", "t12", "pair"):
        template = json.loads((TEMPLATES / f"{name}.template.json").read_text(encoding="utf-8"))
        fields = [k for k in template if not k.startswith("_")]
        assert fields and all(f"_{k}" in template for k in fields), (name, [k for k in fields if f"_{k}" not in template])
        assert "_about" in template and "BY HAND" in template["_about"] and "do not copy values from paperbound" in template["_about"].lower()


def test_specific_problems_are_named_by_where_they_are(tmp_path):
    root = heldout_tree(tmp_path)
    p = root / "rent_roll" / "rent_001.json"
    label = json.loads(p.read_text(encoding="utf-8"))
    del label["rows"][2]["cells"]["sqft"]
    label["rows"][3]["cells"]["lease_end"] = "03/31/2028"
    label["rows"][4]["page"] = 0
    label["mapping"]["7"] = "square_feet"
    label["mapping"]["40"] = "unit"
    label["expected_flags"] = ["row_math_mismatch"]
    label["metrics"]["occupancy"] = 0.5
    p.write_text(json.dumps(label), encoding="utf-8")
    errors = " | ".join(labels.validate(p).errors)
    for piece in ("rows[2].cells.sqft: missing", "rows[3].cells.lease_end: must be an ISO date", "rows[4].page: is missing or not a page number",
                  "mapping.7: 'square_feet' is not one of", "mapping.40: there are only 8 headers", "expected_flags: 'row_math_mismatch' is not one of",
                  "metrics.occupancy: unknown metric"):
        assert piece in errors, (piece, errors)


def test_t12_problems_are_named(tmp_path):
    root = heldout_tree(tmp_path)
    p = root / "t12" / "t12_006.json"
    label = json.loads(p.read_text(encoding="utf-8"))
    label["months"] = label["months"][:11]
    label["lines"][1]["category"] = "rent"
    label["lines"][2]["months"] = [1, 2, 3]
    label["expected_flags"] = {"Not A Line": ["noi_mismatch"], label["lines"][0]["label"]: ["rent_is_odd"]}
    label["expected_anomalies"] = [{"label": label["lines"][0]["label"], "month": "Smarch-99"}]
    p.write_text(json.dumps(label), encoding="utf-8")
    errors = " | ".join(labels.validate(p).errors)
    for piece in ("months: must list exactly 12", "lines[1].category: must be one of", "lines[2].months: must be 12 numbers",
                  "expected_flags.Not A Line: is not the label of any line", "'rent_is_odd' is not one of", "expected_anomalies: each entry needs"):
        assert piece in errors, (piece, errors)


def test_a_pair_must_name_documents_that_exist_and_add_up(tmp_path):
    root = heldout_tree(tmp_path)
    p = root / "pairs" / "pair_001.json"
    label = json.loads(p.read_text(encoding="utf-8"))
    label["t12"] = "no_such_stem"
    label["difference"] = label["difference"] + 100
    label["tolerance"] = 5
    p.write_text(json.dumps(label), encoding="utf-8")
    errors = " | ".join(labels.validate(p).errors)
    assert "t12: t12/no_such_stem.pdf and t12/no_such_stem.json must both exist" in errors
    assert "difference: is not t12_base_rent minus rent_roll_total_annual_rent" in errors and "tolerance: must be a share between 0 and 1" in errors


def test_a_missing_pdf_or_a_wrong_id_is_an_error_and_missing_metrics_only_a_note(tmp_path):
    root = heldout_tree(tmp_path)
    p = root / "rent_roll" / "rent_003.json"
    label = json.loads(p.read_text(encoding="utf-8"))
    label["id"] = "something_else"
    label["metrics"] = {}
    label["totals"] = {}
    p.write_text(json.dumps(label), encoding="utf-8")
    (root / "rent_roll" / "rent_003.pdf").unlink()
    result = labels.validate(p)
    assert any("id: must be the file name" in e for e in result.errors) and any("rent_003.pdf is not next to this label file" in e for e in result.errors)
    assert any(w.startswith("metrics: not labeled") for w in result.warnings) and any(w.startswith("totals: not labeled") for w in result.warnings)
    assert labels.validate(root / "rent_roll" / "missing.json").errors[0].startswith("cannot read it as JSON")
    (root / "weird").mkdir()
    (root / "weird" / "a.json").write_text("{}")
    assert "cannot tell what kind of label" in labels.validate(root / "weird" / "a.json").errors[0]


def test_check_labels_reports_file_by_file_and_exits_nonzero_on_a_problem(tmp_path):
    root = heldout_tree(tmp_path)
    ok = subprocess.run([sys.executable, str(SCRIPTS / "check_labels.py"), "--root", str(root)], capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.count("OK   ") == 8 and "8 of 8 label file(s) are complete." in ok.stdout
    shutil.copy(TEMPLATES / "t12.template.json", root / "t12" / "blank.json")
    bad = subprocess.run([sys.executable, str(SCRIPTS / "check_labels.py"), "--root", str(root)], capture_output=True, text=True)
    assert bad.returncode == 1 and "ERROR" in bad.stdout and "lines: is empty" in bad.stdout and "1 need fixing" in bad.stdout
    empty = subprocess.run([sys.executable, str(SCRIPTS / "check_labels.py"), "--root", str(tmp_path / "nowhere")], capture_output=True, text=True)
    assert empty.returncode == 0 and "no label files found" in empty.stdout


# ---------------------------------------------------------------------------------------------------------------
# --heldout
# ---------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def heldout_env(tmp_path, monkeypatch):
    root = heldout_tree(tmp_path)
    results = tmp_path / "results"
    results.mkdir()
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"saved": "before", "metrics": {"generic/generic/all/field_accuracy": 0.93}}), encoding="utf-8")
    monkeypatch.setattr(cre, "HELDOUT", root)
    monkeypatch.setattr(report, "RESULTS_DIR", results)
    monkeypatch.setattr(report, "LATEST_MD", results / "latest.md")
    monkeypatch.setattr(report, "BASELINE", baseline)
    monkeypatch.setattr(run.config, "OPENAI_API_KEY", "key")
    monkeypatch.setattr(run.Budget, "install", lambda self: None)
    monkeypatch.setattr(run.Budget, "_save", lambda self: None)
    install_oracle(monkeypatch)
    yield {"root": root, "results": results, "baseline": baseline}
    cre.clear_cache()


def test_heldout_runs_only_the_held_out_set_and_reports_it_apart(heldout_env, capsys):
    status = run.main(["--heldout", "--budget", "50"])
    out = capsys.readouterr().out
    assert status == 0, out
    assert "rent_roll (heldout): 3 fixtures" in out and "t12 (heldout): 3 fixtures" in out and "crosscheck (heldout)" in out
    assert "synthetic" not in out and "cuad" not in out and "generic" not in out
    names = sorted(p.name for p in heldout_env["results"].iterdir())
    assert any(n.startswith("heldout_rent_roll_") for n in names) and any(n.startswith("heldout_t12_") for n in names)
    assert any(n.startswith("heldout_crosscheck_") for n in names) and "heldout_latest.md" in names
    assert any(n.startswith("failures_heldout_") for n in names)
    assert "latest.md" not in names, "the dev and public latest.md is not rebuilt"
    md = (heldout_env["results"] / "heldout_latest.md").read_text(encoding="utf-8")
    assert "Held-out results (private)" in md and "Do not change code, prompts or thresholds" in md and "Header mapping" in md
    saved = json.loads(next(heldout_env["results"].glob("heldout_rent_roll_*.json")).read_text(encoding="utf-8"))
    assert saved["dataset"] == "heldout" and saved["n_docs"] == 3 and saved["groups"]["all"]["cell_accuracy"] == 1.0
    assert "baseline untouched" in out


def test_heldout_never_touches_the_baseline_and_cannot_be_combined_with_baseline_options(heldout_env, capsys):
    before = heldout_env["baseline"].read_bytes()
    assert run.main(["--heldout", "--budget", "50"]) == 0
    assert heldout_env["baseline"].read_bytes() == before
    assert "DROP" not in capsys.readouterr().out, "no comparison with the baseline either"
    for option in (["--save-baseline"], ["--median-baseline", "x.json"], ["--merge-median-baseline", "x.json"], ["--rescore", "x.json"]):
        with pytest.raises(SystemExit, match="never updates or reads the baseline"):
            run.main(["--heldout", *option])
    assert heldout_env["baseline"].read_bytes() == before
    with pytest.raises(SystemExit, match="rent roll, T-12 and cross-check suites only"):
        run.main(["--heldout", "--suite", "synthetic"])


def test_heldout_results_do_not_reach_the_dev_view_or_the_eval_page(heldout_env):
    from paperbound import evals_page

    run.main(["--heldout", "--budget", "50"])
    assert report.latest_runs(heldout_env["results"]) == {}
    assert "Rent roll" not in evals_page.render(heldout_env["results"]).split("<h2>Demo fixtures</h2>")[0]


def test_heldout_with_nothing_in_it_does_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cre, "HELDOUT", tmp_path / "heldout")
    monkeypatch.setattr(run.config, "OPENAI_API_KEY", "")  # would exit if it got as far as needing a key
    assert run.main(["--heldout"]) == 0
    assert "no held-out label files found" in capsys.readouterr().out


def test_heldout_refuses_incomplete_labels_before_any_model_call(heldout_env, capsys):
    shutil.copy(TEMPLATES / "rent_roll.template.json", heldout_env["root"] / "rent_roll" / "blank.json")
    calls = []
    original = cre.run_document
    cre.run_document = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
    try:
        status = run.main(["--heldout", "--budget", "50"])
    finally:
        cre.run_document = original
    out = capsys.readouterr().out
    assert status == 2 and "1 of 9 label file(s) are incomplete" in out and calls == []
    assert list(heldout_env["results"].iterdir()) == []


def test_heldout_uses_the_date_in_each_label(heldout_env):
    p = heldout_env["root"] / "rent_roll" / "rent_001.json"
    label = json.loads(p.read_text(encoding="utf-8"))
    assert label["as_of"] == "2026-10-22"
    fixture = cre.find("rent_roll", "rent_001", heldout_env["root"])
    assert fixture.pdf.name == "rent_001.pdf" and fixture.kind == "rent_roll"
    assert cre.label_date(label, cre.date(2030, 1, 1)).isoformat() == "2026-10-22"
    assert cre.label_date({}, cre.date(2030, 1, 1)).isoformat() == "2030-01-01"


def test_the_gitignore_keeps_the_documents_and_results_private_but_not_the_readmes():
    """Asked of git about paths that need not exist: no file under evals/heldout is opened."""
    def ignored(path: str) -> bool:
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0

    for private in ("evals/heldout/rent_roll/prop_a.pdf", "evals/heldout/rent_roll/prop_a.json", "evals/heldout/t12/prop_a.json",
                    "evals/heldout/pairs/pair_a.json", "evals/results/heldout_rent_roll_20261028_010101.json",
                    "evals/results/failures_heldout_t12_20261028_010101.json", "evals/results/heldout_latest.md"):
        assert ignored(private), private
    for public in ("evals/heldout/README.md", "evals/heldout/rent_roll/README.md", "evals/heldout/t12/README.md", "evals/heldout/pairs/README.md",
                   "evals/label_templates/t12.template.json", "evals/results/latest.md", "evals/results/rent_roll_20261028_010101.json"):
        assert not ignored(public), public
