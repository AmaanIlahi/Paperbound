"""Narration templates (shared by recorded samples and live runs) and the /evals page."""

import json

import pytest
from fastapi.testclient import TestClient

from paperbound import evals_page
from paperbound.api.main import app
from paperbound.demo import replay
from paperbound.narration import DEFAULT_ACTION_TEXT, Narrator, draft_narration

FIXTURES = ("lease", "insurance_certificate")


@pytest.fixture(params=FIXTURES)
def snap(request):
    return replay.load_snapshot(request.param)


def test_every_recorded_sample_has_narration_for_every_step_and_the_templates_cover_the_same_keys(snap):
    drafted = draft_narration(snap)
    assert set(drafted["steps"]) == {"parse", "classify", "extract", "validate", "obligations", "impact", "planner", "gate"}
    committed = snap["narration"]
    assert set(committed["steps"]) == set(drafted["steps"]) and set(committed["actions"]) == set(drafted["actions"])
    assert all(len(t) > 60 for t in drafted["steps"].values())
    assert drafted["actions"] == DEFAULT_ACTION_TEXT


def test_templates_state_what_the_step_really_produced(snap):
    text = draft_narration(snap)["steps"]
    present = [f for f in snap["fields"] if f["value"] is not None]
    assert f"returned {len(present)} fields" in text["extract"]
    assert f"{len(snap['obligations'])} obligation(s)" in text["obligations"]
    assert f"proposes {len(snap['actions'])} action(s)" in text["planner"]
    assert snap["brief"]["lines"][0]["text"] in text["impact"]
    assert f"as of {snap['as_of']}" in text["impact"]


def test_live_and_recorded_framing_differ_only_where_they_must():
    n = Narrator("lease", "lease.pdf", 4, "2026-10-22")
    assert "recorded run" in n.intro() and "live run" in n.intro(live=True) and "your lease" in n.intro(live=True)
    assert "inside your browser" in n.gate(set()) and "inside your browser" not in n.gate(set(), live=True)
    assert "Approving one notice rejects the other" in n.gate({"notice_choice"}, live=True)
    assert n.outro(2, 100, 0.0016).startswith("That was the whole run: 2 model call(s), 100 tokens, $0.0016.")


def test_narration_handles_unsupported_failed_and_skipped_steps():
    n = Narrator("document", "x.pdf", 1, "2026-10-22")
    assert "stops here instead of guessing" in n.classify({"method": "llm", "reason": "A recipe."}, "other")
    assert "did not run" in n.skipped("validate", "Extraction failed, nothing to check.")
    assert "openai is down" in n.failed("extract", "openai is down")
    assert "not called" in n.no_planner()


# ---- /evals ----

def write_result(directory, name, **summary):
    (directory / f"{name}.json").write_text(json.dumps({"summary": summary}), encoding="utf-8")


def test_evals_page_shows_the_newest_result_per_fixture(tmp_path):
    write_result(tmp_path, "lease_20261022_010000", pdf="lease.pdf", doc_type="lease", fields_matched="10/14", cost_usd=0.1)
    write_result(tmp_path, "lease_20261022_090000", pdf="lease.pdf", doc_type="lease", fields_matched="14/14", classification_match=True, cost_usd=0.00155)
    write_result(tmp_path, "lease_injected_20261022_090000", pdf="lease_injected.pdf", doc_type="lease", actions_match=False)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    rows = evals_page.latest_results(tmp_path)
    assert [r["pdf"] for r in rows] == ["lease.pdf", "lease_injected.pdf"] and rows[0]["fields_matched"] == "14/14"
    html = evals_page.render(tmp_path)
    assert "14/14" in html and "10/14" not in html and "0.00155" in html
    assert 'class="chip ok"' in html and 'class="chip bad"' in html


def test_evals_page_escapes_what_it_shows_and_has_an_empty_state(tmp_path):
    write_result(tmp_path, "x_20261022_090000", pdf="<script>alert(1)</script>", doc_type="lease")
    assert "<script>alert(1)</script>" not in evals_page.render(tmp_path) and "&lt;script&gt;" in evals_page.render(tmp_path)
    assert "No saved results yet" in evals_page.render(tmp_path / "nothing_here")


def test_the_evals_route_serves_the_committed_results():
    response = TestClient(app).get("/evals")
    assert response.status_code == 200 and "lease.pdf" in response.text and "Evals" in response.text
    assert 'href="/web/style.css"' in response.text
