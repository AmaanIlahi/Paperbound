"""POST /documents classifies first, then runs the matching schema, brief and planner. Unsupported types stop early."""

import io
from datetime import date

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from paperbound.agent.planner import plan_actions
from paperbound.api import routes
from paperbound.api.main import app
from paperbound.db.models import Base, Document, Field, ProposedAction
from paperbound.db.session import get_session
from paperbound.pipeline.classify import Classification, classify_document
from paperbound.pipeline.runner import make_parse_step, run_pipeline
from paperbound.storage import LocalStorage, get_storage
from tests.coi_fixture import DEMO, fake_coi_extractor
from tests.test_classify import FakeLLM, NoLLM
from tests.test_coi import full_response, plan_inputs
from tests.test_planner import FakeClient
from tests.test_upload_flow import fake_extractor, fake_planner

TODAY = date(2026, 10, 22)


def recipe_pdf() -> bytes:
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    pdf.drawString(72, 700, "Grandma's pancake recipe: flour, eggs, milk, butter.")
    pdf.drawString(72, 680, "Whisk, rest the batter for ten minutes, then fry.")
    pdf.save()
    return buf.getvalue()


def must_not_extract(pages):
    raise AssertionError("an unsupported document must not reach extraction")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    def override():
        with Session() as session:
            yield session

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_storage] = lambda: LocalStorage(tmp_path)
    yield TestClient(app), Session, monkeypatch
    app.dependency_overrides.clear()


def use_pipeline(monkeypatch, **kwargs):
    monkeypatch.setattr(
        routes, "run_pipeline",
        lambda pages, step, existing_obligations=None: run_pipeline(
            pages, step, existing_obligations=existing_obligations, today=TODAY, **kwargs
        ),
    )


def upload(client, data: bytes, name="doc.pdf"):
    return client.post("/documents", files={"file": (name, data, "application/pdf")}).json()


# ---- "other" stops after classify ----

def test_other_stops_after_classify_with_status_done_and_the_message(env):
    http, Session, monkeypatch = env
    llm = FakeLLM("other", "A pancake recipe.")
    use_pipeline(monkeypatch, classifier=lambda pages: classify_document(pages, client=llm),
                 extractor=must_not_extract, planner=lambda *a, **k: pytest.fail("planner must not run"))
    doc = upload(http, recipe_pdf())
    assert doc["status"] == "done" and doc["doc_type"] == "other" and doc["message"] == "document type not supported yet"
    run_id = doc["run_id"]
    assert [s["stage"] for s in http.get(f"/runs/{run_id}/steps").json()] == ["parse", "classify"]
    classify_step = http.get(f"/runs/{run_id}/steps").json()[1]
    assert classify_step["summary"] == "other (llm)" and "pancake recipe" in classify_step["reason"]
    assert "not supported yet" in classify_step["reason"] and classify_step["tokens"] == 300 and classify_step["cost_usd"] > 0
    assert http.get(f"/runs/{run_id}/fields").json() == [] and http.get(f"/runs/{run_id}/actions").json() == []
    assert http.get(f"/runs/{run_id}/obligations").json() == []
    assert http.get(f"/runs/{run_id}/brief").json() == {"lines": [], "skipped": []}
    run = http.get(f"/runs/{run_id}").json()
    assert run["status"] == "done" and run["doc_type"] == "other" and run["message"] == "document type not supported yet"
    assert run["classification"]["summary"] == "other (llm)"
    with Session() as s:
        assert s.scalar(select(Document)).doc_type == "other"
        assert s.scalars(select(Field)).all() == [] and s.scalars(select(ProposedAction)).all() == []


def test_the_pages_of_an_unsupported_document_are_still_viewable(env):
    http, _, monkeypatch = env
    use_pipeline(monkeypatch, classifier=lambda pages: Classification("other", "llm", "x"), extractor=must_not_extract)
    doc = upload(http, recipe_pdf())
    page = http.get(f"/documents/{doc['document_id']}/pages/1").json()
    assert page["words"] and page["image_url"]


def test_a_failed_classifier_gives_a_partial_run_and_skips_everything_else(env):
    http, _, monkeypatch = env

    def boom(pages):
        raise RuntimeError("openai is down")

    use_pipeline(monkeypatch, classifier=boom, extractor=must_not_extract)
    doc = upload(http, recipe_pdf())
    assert doc["status"] == "partial" and doc["doc_type"] is None
    steps = http.get(f"/runs/{doc['run_id']}/steps").json()
    assert [(s["stage"], s["status"]) for s in steps] == [
        ("parse", "done"), ("classify", "failed"), ("extract", "skipped"), ("validate", "skipped"),
        ("obligations", "skipped"), ("impact", "skipped"), ("planner", "skipped"),
    ]
    assert "openai is down" in steps[1]["error"]


# ---- certificates take the certificate path ----

def test_a_certificate_is_classified_extracted_briefed_and_planned(env):
    http, Session, monkeypatch = env
    ids = plan_inputs()[2]
    planner_fn = lambda fields, obligations, existing, **kw: plan_actions(  # noqa: E731
        fields, obligations, existing, client=FakeClient(full_response(ids)), **kw
    )
    use_pipeline(monkeypatch, classifier=lambda pages: classify_document(pages, client=NoLLM()),
                 extractor=fake_coi_extractor, planner=planner_fn)
    doc = upload(http, (DEMO / "insurance_certificate.pdf").read_bytes(), "coi.pdf")
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


def test_a_lease_still_takes_the_lease_path(env):
    http, _, monkeypatch = env
    use_pipeline(monkeypatch, classifier=lambda pages: classify_document(pages, client=NoLLM()),
                 extractor=fake_extractor, planner=fake_planner)
    doc = upload(http, (DEMO / "lease.pdf").read_bytes(), "lease.pdf")
    assert doc["doc_type"] == "lease" and http.get(f"/runs/{doc['run_id']}").json()["doc_type"] == "lease"


def test_old_runs_without_a_stored_type_are_treated_as_leases(env):
    http, Session, monkeypatch = env
    use_pipeline(monkeypatch, classifier=lambda pages: Classification("lease", "heuristic", "x"),
                 extractor=fake_extractor, planner=fake_planner)
    doc = upload(http, (DEMO / "lease.pdf").read_bytes())
    with Session() as s:
        s.scalar(select(Document)).doc_type = None  # what runs from before classification look like
        s.commit()
    brief = http.get(f"/runs/{doc['run_id']}/brief", params={"today": "2026-10-22"}).json()
    assert {ln["key"] for ln in brief["lines"]} >= {"deadline", "days_remaining"}
    assert http.get(f"/runs/{doc['run_id']}").json()["message"] is None
