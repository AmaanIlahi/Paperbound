"""A queued upload is classified first, then runs the matching schema, brief and planner. Unsupported types take the generic path."""

import io

import pytest
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from sqlalchemy import select

from paperbound.agent.planner import plan_actions
from paperbound.db.models import Document, Field, ProposedAction
from paperbound.pipeline.classify import Classification, classify_document
from tests.coi_fixture import DEMO, fake_coi_extractor
from tests.generic_fakes import empty_fallback, fake_generic_extractor
from tests.lease_fakes import TODAY, fake_extractor, fake_planner
from tests.live import live  # noqa: F401
from tests.test_classify import FakeLLM, NoLLM
from tests.test_coi import full_response, plan_inputs
from tests.test_planner import FakeClient


def recipe_pdf() -> bytes:
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    pdf.drawString(72, 700, "Grandma's pancake recipe: flour, eggs, milk, butter.")
    pdf.drawString(72, 680, "Whisk, rest the batter for ten minutes, then fry.")
    pdf.save()
    return buf.getvalue()


def must_not_extract(pages):
    raise AssertionError("this document must not reach extraction")


def use_pipeline(live, **kwargs):
    live.use_pipeline(lambda: {"today": TODAY, **kwargs})


def upload(live, data: bytes, name="doc.pdf"):
    """Upload, let the worker run it, and return what the run endpoint says about it (plus the ids)."""
    body = live.upload_and_run(data, name)
    run = live.client.get(f"/runs/{body['run_id']}").json()
    return {**body, **{k: run[k] for k in ("status", "doc_type", "message")}}


# ---- an unsupported document takes the generic path ----

def test_an_unsupported_document_is_routed_to_the_generic_fallback_not_a_typed_schema(live):
    http, Session = live.client, live.Session
    llm = FakeLLM("other", "A pancake recipe.", type_label="pancake recipe")
    use_pipeline(live, classifier=lambda pages: classify_document(pages, client=llm),
                 extractor=lambda pages: fake_generic_extractor(pages, empty_fallback()),
                 planner=lambda *a, **k: pytest.fail("the typed planner must not run"))
    doc = upload(live, recipe_pdf())
    assert doc["status"] == "done" and doc["doc_type"] == "generic" and doc["message"] is None
    run_id = doc["run_id"]
    steps = http.get(f"/runs/{run_id}/steps").json()
    assert [s["stage"] for s in steps] == ["parse", "classify", "extract", "validate", "obligations", "impact", "planner"]
    classify_step = steps[1]
    assert classify_step["summary"] == "generic (llm)" and "pancake recipe" in classify_step["reason"]
    assert "generic fallback" in classify_step["reason"] and classify_step["tokens"] == 300 and classify_step["cost_usd"] > 0
    run = http.get(f"/runs/{run_id}").json()
    assert run["status"] == "done" and run["doc_type"] == "generic" and run["type_label"] == "pancake recipe"
    assert run["classification"]["summary"] == "generic (llm)"
    with Session() as s:
        document = s.scalar(select(Document))
        assert document.doc_type == "generic" and document.type_label == "pancake recipe"
        assert [a.tool for a in s.scalars(select(ProposedAction))] == ["flag_for_review"]
    assert http.get(f"/runs/{run_id}/obligations").json() == []
    assert http.get(f"/runs/{run_id}/brief").json() == {"lines": [], "skipped": []}


def test_the_pages_of_a_generic_document_are_still_viewable(live):
    http, Session = live.client, live.Session
    use_pipeline(live, classifier=lambda pages: Classification("generic", "llm", "x", type_label="recipe"),
                 extractor=lambda pages: fake_generic_extractor(pages, empty_fallback()))
    doc = upload(live, recipe_pdf())
    page = http.get(f"/documents/{doc['document_id']}/pages/1").json()
    assert page["words"] and page["image_url"]


def test_a_failed_classifier_gives_a_partial_run_and_skips_everything_else(live):
    http, Session = live.client, live.Session

    def boom(pages):
        raise RuntimeError("openai is down")

    use_pipeline(live, classifier=boom, extractor=must_not_extract)
    doc = upload(live, recipe_pdf())
    assert doc["status"] == "partial" and doc["doc_type"] is None
    steps = http.get(f"/runs/{doc['run_id']}/steps").json()
    assert [(s["stage"], s["status"]) for s in steps] == [
        ("parse", "done"), ("classify", "failed"), ("extract", "skipped"), ("validate", "skipped"),
        ("obligations", "skipped"), ("impact", "skipped"), ("planner", "skipped"),
    ]
    assert "openai is down" in steps[1]["error"]


# ---- certificates take the certificate path ----

def test_a_certificate_is_classified_extracted_briefed_and_planned(live):
    http, Session = live.client, live.Session
    ids = plan_inputs()[2]
    planner_fn = lambda fields, obligations, existing, **kw: plan_actions(  # noqa: E731
        fields, obligations, existing, client=FakeClient(full_response(ids)), **kw
    )
    use_pipeline(live, classifier=lambda pages: classify_document(pages, client=NoLLM()),
                 extractor=fake_coi_extractor, planner=planner_fn)
    doc = upload(live, (DEMO / "insurance_certificate.pdf").read_bytes(), "coi.pdf")
    assert doc["status"] == "done" and doc["doc_type"] == "insurance_certificate" and doc["message"] is None
    run_id = doc["run_id"]
    steps = http.get(f"/runs/{run_id}/steps").json()
    assert [s["stage"] for s in steps] == ["parse", "classify", "extract", "validate", "obligations", "impact", "planner"]
    assert steps[1]["summary"] == "insurance_certificate (heuristic)" and steps[1]["tokens"] == 0
    assert steps[2]["summary"] == "4/4 top-level fields returned"

    names = [f["name"] for f in http.get(f"/runs/{run_id}/fields").json()]
    assert names[:3] == ["insured", "certificate_holder", "producer"] and "policy_2.each_occurrence_limit" in names
    obligations = http.get(f"/runs/{run_id}/obligations").json()
    assert [(o["kind"], o["due_date"]) for o in obligations] == [("expiration", "2027-01-15"), ("expiration", "2027-03-01")]

    actions = http.get(f"/runs/{run_id}/actions").json()
    assert [a["tool"] for a in actions] == ["create_calendar_event"] * 3 + ["draft_email", "upsert_record"]
    assert f"/#run={run_id}&page=1" in actions[0]["args"]["description"]
    assert all(s["bboxes"] and s["page_no"] == 1 for a in actions for s in a["sources"])

    brief = http.get(f"/runs/{run_id}/brief", params={"today": "2026-10-22"}).json()
    lines = {ln["key"]: ln for ln in brief["lines"]}
    assert lines["earliest_expiration"]["text"] == "January 15, 2027" and lines["days_remaining"]["value"] == 85
    assert lines["limit_check_1"]["value"] is True and brief["skipped"] == []
    assert [s["name"] for s in lines["limit_check_2"]["sources"]] == ["policy_2.each_occurrence_limit", "policy_2.type"]

    run = http.get(f"/runs/{run_id}").json()
    assert run["doc_type"] == "insurance_certificate" and run["message"] is None and run["page_count"] == 1
    with Session() as s:
        assert s.scalar(select(Document)).doc_type == "insurance_certificate"


def test_a_lease_still_takes_the_lease_path(live):
    http, Session = live.client, live.Session
    use_pipeline(live, classifier=lambda pages: classify_document(pages, client=NoLLM()),
                 extractor=fake_extractor, planner=fake_planner)
    doc = upload(live, (DEMO / "lease.pdf").read_bytes(), "lease.pdf")
    assert doc["doc_type"] == "lease" and http.get(f"/runs/{doc['run_id']}").json()["doc_type"] == "lease"


def test_old_runs_without_a_stored_type_are_treated_as_leases(live):
    http, Session = live.client, live.Session
    use_pipeline(live, classifier=lambda pages: Classification("lease", "heuristic", "x"),
                 extractor=fake_extractor, planner=fake_planner)
    doc = upload(live, (DEMO / "lease.pdf").read_bytes())
    with Session() as s:
        s.scalar(select(Document)).doc_type = None  # what runs from before classification look like
        s.commit()
    brief = http.get(f"/runs/{doc['run_id']}/brief", params={"today": "2026-10-22"}).json()
    assert {ln["key"] for ln in brief["lines"]} >= {"deadline", "days_remaining"}
    assert http.get(f"/runs/{doc['run_id']}").json()["message"] is None
