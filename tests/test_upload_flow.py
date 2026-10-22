"""POST /documents on the real lease fixture with a fake LLM: checks what the route persists."""

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from paperbound.agent.planner import plan_actions
from paperbound.api import routes
from paperbound.api.main import app
from paperbound.db.models import Base, ProposedAction
from paperbound.db.session import get_session
from paperbound.pipeline.extract import ExtractionResult
from paperbound.pipeline.runner import run_pipeline
from paperbound.schemas.lease import LeaseFields
from paperbound.storage import LocalStorage, get_storage
from tests.test_planner import FakeClient, call, cal, email, response

LEASE = Path(__file__).resolve().parent.parent / "fixtures" / "demo" / "lease.pdf"
TODAY = date(2026, 10, 22)

QUOTES = {
    "landlord": ("Harborview Properties LLC", "Harborview Properties LLC", 1),
    "tenant": ("Brightline Studio Inc.", "Brightline Studio Inc.", 1),
    "premises_address": ("220 Hudson Street, New York, NY 10013", "located at 220 Hudson Street, New York, NY 10013", 1),
    "start_date": ("2024-04-01", "commencing on April 1, 2024", 2),
    "end_date": ("2027-03-31", "expiring at 11:59 p.m. on March 31, 2027", 2),
    "rent_amount": (8500.0, "initial rate of Eight Thousand Five Hundred Dollars ($8,500.00) per month", 2),
    "rent_period": ("monthly", "($8,500.00) per month", 2),
    "notice_period_days": (90, "at least ninety (90) days before the Expiration Date", 3),
    "holdover_rate_percent": (150.0, "one hundred fifty percent (150%) of the Base Rent in effect on the Expiration Date", 3),
    "security_deposit": (17000.0, "deposit with Landlord the sum of $17,000.00 as security", 2),
    "notice_method": ("certified mail or nationally recognized overnight courier",
                      "Notice shall be delivered by certified mail or nationally recognized overnight courier", 3),
    "notice_section": ("Section 11", "11. Renewal; Termination at Expiration", 3),
    "rent_escalation_percent": (3.0, "Base Rent shall increase by three percent (3%)", 2),
    "current_rent": (9017.65, "3 Apr 1, 2026 - Mar 31, 2027 $9,017.65", 2),
}


def fake_extractor(pages):
    data = {n: {"value": v, "source_quote": q, "page": p} for n, (v, q, p) in QUOTES.items()}
    return ExtractionResult(LeaseFields.model_validate(data), "fake-model", 10, 0.0)


def fake_planner(fields, obligations, existing, **kwargs):
    ids = [1, 2]
    reminders = [
        call(f"c{i}", "create_calendar_event", **cal(d, source_field_ids=ids))
        for i, d in enumerate(("2026-11-01", "2026-12-01", "2026-12-17"))
    ]
    client = FakeClient(
        response(
            *reminders,
            call("e1", "draft_email", **email("renew", source_field_ids=ids)),
            call("e2", "draft_email", **email("terminate", source_field_ids=ids)),
        )
    )
    return plan_actions(fields, obligations, existing, client=client, **kwargs)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    def override():
        with Session() as session:
            yield session

    monkeypatch.setattr(
        routes, "run_pipeline",
        lambda pages, step, existing_obligations=None: run_pipeline(
            pages, step, extractor=fake_extractor, planner=fake_planner, existing_obligations=existing_obligations, today=TODAY
        ),
    )
    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_storage] = lambda: LocalStorage(tmp_path)
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def upload(client):
    with LEASE.open("rb") as f:
        return client.post("/documents", files={"file": ("lease.pdf", f, "application/pdf")}).json()


def test_upload_persists_the_staged_plan_with_groups_and_resolved_links(client):
    http, _ = client
    doc = upload(http)
    assert doc["status"] == "done"
    run_id = doc["run_id"]
    actions = http.get(f"/runs/{run_id}/actions").json()
    assert [a["tool"] for a in actions] == ["create_calendar_event"] * 3 + ["draft_email"] * 2 + ["upsert_record"]
    assert {a["group_key"] for a in actions[:3]} == {"reminders"} and {a["group_mode"] for a in actions[3:5]} == {"exclusive"}
    assert all(a["status"] == "pending" for a in actions)
    description = actions[0]["args"]["description"]
    assert f"/#run={run_id}&page=3" in description and "{{RUN_ID}}" not in description
    # every source of the first reminder carries its own page, so each "because of" link can go somewhere different
    pages = {s["name"]: s["page_no"] for s in actions[0]["sources"]}
    assert pages["end_date"] == 2 and pages["notice_period_days"] == 3 and pages["notice_section"] == 3
    assert all(s["bboxes"] for s in actions[0]["sources"])


def test_run_endpoint_and_brief_work_for_a_saved_run(client):
    http, _ = client
    run_id = upload(http)["run_id"]
    assert http.get(f"/runs/{run_id}").json()["page_count"] == 4
    steps = [s["stage"] for s in http.get(f"/runs/{run_id}/steps").json()]
    assert steps == ["parse", "extract", "validate", "obligations", "impact", "planner"]
    brief = http.get(f"/runs/{run_id}/brief", params={"today": "2026-10-22"}).json()
    lines = {ln["key"]: ln for ln in brief["lines"]}
    assert lines["holdover_rent"]["value"] == 13526.48 and brief["skipped"] == []
    assert all(ln["sources"] for ln in brief["lines"])


def test_choosing_one_notice_through_the_saved_run_rejects_the_other(client):
    http, Session = client
    run_id = upload(http)["run_id"]
    actions = http.get(f"/runs/{run_id}/actions").json()
    renew, terminate = [a for a in actions if a["tool"] == "draft_email"]
    assert http.post(f"/actions/{renew['id']}/approve").json()["status"] == "executed"
    assert http.post(f"/actions/{terminate['id']}/approve").status_code == 409
    with Session() as s:
        by_id = {a.id: a.status for a in s.scalars(select(ProposedAction))}
    assert by_id[terminate["id"]] == "rejected"
    assert sum(1 for a in actions if a["tool"] == "create_calendar_event" and by_id[a["id"]] == "pending") == 3
