"""A rent roll uploaded through the public API: the worker detects the table in the stored PDF, the stream carries the rows,
the brief is recomputed from the stored rows, and the CSV holds only clean rows. Nobody else can read any of it."""

import json
from pathlib import Path

from paperbound.pipeline import rent_roll
from paperbound.pipeline.classify import Classification
from paperbound.pipeline.mapping import MappingResult
from tests.lease_fakes import TODAY
from tests.live import live  # noqa: F401

FIXTURE = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "rent_roll" / "rr_003"


def use_rent_roll(live):
    exp = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
    mapping = {int(k): v for k, v in exp["mapping"].items()}
    # only the model call is replaced: the worker reads the stored PDF and the real table code runs on it
    live.monkeypatch.setattr(rent_roll, "map_rent_roll_columns", lambda header, rows, client=None: MappingResult(mapping, "gpt-4o-mini", 100, 0.0001))
    live.use_pipeline(lambda: {"classifier": lambda pages: Classification("rent_roll", "heuristic", "test"), "today": TODAY})
    return exp


def test_a_rent_roll_runs_end_to_end(live):
    exp = use_rent_roll(live)
    run = live.upload_and_run((FIXTURE / "doc.pdf").read_bytes(), "rent_roll.pdf")
    rid = run["run_id"]
    assert live.client.get(f"/runs/{rid}").json()["status"] == "done"
    assert live.client.get(f"/runs/{rid}").json()["doc_type"] == "rent_roll"

    fields = live.client.get(f"/runs/{rid}/fields").json()
    rows = [f for f in fields if f["name"].startswith("row_")]
    assert len(rows) == len(exp["rows"]) == 22
    assert rows[0]["value"]["unit"] == "Suite 100" and rows[0]["page_no"] == 1 and len(rows[0]["bbox"]) == 4
    totals = next(f for f in fields if f["name"] == "totals")
    assert totals["flags"] == ["totals_mismatch"]  # rr_003 plants it
    assert next(f for f in fields if f["name"] == "mapping")["value"]["columns"][0] == {"column": 0, "header": "Suite", "field": "unit"}

    brief = live.client.get(f"/runs/{rid}/brief", params={"today": "2026-10-22"}).json()
    lines = {ln["key"]: ln for ln in brief["lines"]}
    assert lines["total_annual_rent"]["value"] == exp["metrics"]["total_annual_rent"]
    assert lines["occupancy_units"]["sources"][0]["name"] == "row_1" and lines["occupancy_units"]["sources"][0]["bboxes"]
    assert lines["totals_check"]["value"] is False

    actions = live.client.get(f"/runs/{rid}/actions").json()
    assert {a["tool"] for a in actions} == {"create_calendar_event", "flag_for_review"}
    assert all(a["status"] == "pending" for a in actions)
    assert {a["group_key"] for a in actions if a["tool"] == "create_calendar_event"} <= {"lease_expirations_12m", "lease_expirations_24m"}
    assert all("{{RUN_ID}}" not in a["args"].get("description", "") for a in actions)  # the run id was filled in

    events = live.events(rid)
    names = [n for _, n, _ in events]
    assert names.count("field") == 2 * len(fields) and names[-1] == "done"
    step_events = [d for _, n, d in events if n == "step" and d["stage"] == "extract" and d["status"] == "done"]
    assert "unit row" in step_events[0]["summary"] and "never read or wrote a number" in step_events[0]["narration"]

    response = live.client.get(f"/runs/{rid}/export.csv")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    lines_csv = response.text.strip().split("\n")
    assert lines_csv[0].startswith("row,page,unit,tenant") and len(lines_csv) == 1 + 22  # every row is clean; vacant ones are rows too
    assert all(line.endswith(",totals_mismatch") for line in lines_csv[1:])  # rr_003's stated total does not match its rows


def test_nobody_else_can_read_or_export_the_run(live):
    use_rent_roll(live)
    rid = live.upload_and_run((FIXTURE / "doc.pdf").read_bytes(), "rent_roll.pdf")["run_id"]
    other = live.visitor()
    for path in (f"/runs/{rid}/export.csv", f"/runs/{rid}/fields", f"/runs/{rid}/brief"):
        assert other.get(path).status_code == 404


def test_a_lease_has_no_csv_export(live):
    rid = live.upload_and_run()["run_id"]
    assert live.client.get(f"/runs/{rid}/export.csv").status_code == 404


def test_a_rent_roll_run_plays_through_the_renderer(live, tmp_path):
    import shutil
    import subprocess

    import pytest

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    use_rent_roll(live)
    rid = live.upload_and_run((FIXTURE / "doc.pdf").read_bytes(), "rent_roll.pdf")["run_id"]
    path = tmp_path / "events.json"
    path.write_text(json.dumps([[n, d] for _, n, d in live.events(rid)]), encoding="utf-8")
    script = Path(__file__).parent / "ui" / "rent_roll.test.js"
    done = subprocess.run(["node", str(script), str(path)], capture_output=True, text=True, timeout=90)
    assert done.returncode == 0, done.stdout + done.stderr
