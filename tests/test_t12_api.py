"""A T-12 uploaded through the public API: the worker detects the table in the stored PDF, the stream carries the lines, the brief
is recomputed from them, and the CSV holds only clean lines."""

import json
from pathlib import Path

from paperbound.pipeline import t12
from paperbound.pipeline.classify import Classification
from paperbound.pipeline.mapping import MappingResult
from tests.lease_fakes import TODAY
from tests.live import live  # noqa: F401

FIXTURE = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "t12" / "t12_008"  # capex inside the operating expenses


def use_t12(live):
    exp = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
    want = {ln["label"]: ln["category"] for ln in exp["lines"]}
    live.monkeypatch.setattr(t12, "map_t12_labels", lambda labels, client=None: MappingResult(
        {i: want.get(label) for i, (label, _) in enumerate(labels)}, "gpt-4o-mini", 90, 0.0001))
    live.use_pipeline(lambda: {"classifier": lambda pages: Classification("t12", "heuristic", "test"), "today": TODAY})
    return exp


def test_a_t12_runs_end_to_end(live):
    exp = use_t12(live)
    rid = live.upload_and_run((FIXTURE / "doc.pdf").read_bytes(), "t12.pdf")["run_id"]
    assert live.client.get(f"/runs/{rid}").json()["doc_type"] == "t12" and live.client.get(f"/runs/{rid}").json()["status"] == "done"
    fields = live.client.get(f"/runs/{rid}/fields").json()
    lines = {f["value"]["label"]: f for f in fields if f["name"].startswith("line_")}
    assert len(lines) == len(exp["lines"]) and lines["Rental Income"]["value"]["category"] == "base_rent" and lines["Rental Income"]["bbox"]
    assert lines["Total Operating Expenses"]["flags"] == ["subtotal_mismatch"] and lines["Net Operating Income"]["flags"] == ["noi_mismatch"]
    assert next(f for f in fields if f["name"] == "columns")["value"]["months"][0] == "Oct 2025"

    brief = {ln["key"]: ln for ln in live.client.get(f"/runs/{rid}/brief").json()["lines"]}
    assert brief["noi"]["value"] == exp["expected"]["noi"] and brief["noi"]["sources"][0]["name"].startswith("line_")
    assert brief["checks"]["value"] is False and brief["below_line"]["value"] == exp["expected"]["below_line"]

    actions = live.client.get(f"/runs/{rid}/actions").json()
    assert [a["tool"] for a in actions] == ["flag_for_review"] and "noi_mismatch" in actions[0]["args"]["reason"]
    assert live.client.get(f"/runs/{rid}/obligations").json() == []

    csv_text = live.client.get(f"/runs/{rid}/export.csv").text.strip().split("\n")
    assert csv_text[0].startswith("line,page,label,category,section,Oct 2025,") and len(csv_text) == 1 + len(exp["lines"]) - 2  # the two flagged subtotals are left out
    assert not any("Net Operating Income" in row for row in csv_text)
    assert live.visitor().get(f"/runs/{rid}/export.csv").status_code == 404


def test_a_t12_run_plays_through_the_renderer(live, tmp_path):
    import shutil
    import subprocess

    import pytest

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    use_t12(live)
    rid = live.upload_and_run((FIXTURE / "doc.pdf").read_bytes(), "t12.pdf")["run_id"]
    path = tmp_path / "events.json"
    path.write_text(json.dumps([[n, d] for _, n, d in live.events(rid)]), encoding="utf-8")
    done = subprocess.run(["node", str(Path(__file__).parent / "ui" / "t12.test.js"), str(path)], capture_output=True, text=True, timeout=90)
    assert done.returncode == 0, done.stdout + done.stderr
