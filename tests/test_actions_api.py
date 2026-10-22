import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from paperbound.api.main import app
from paperbound.db.models import (
    ActionResult,
    Base,
    Document,
    Field,
    ProposedAction,
    Run,
    SandboxAction,
)
from paperbound.db.session import get_session
from tests.helpers import log_in, new_visitor


COOKIE, HASH = new_visitor()


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    with Session() as s:
        s.add(Document(id=1, filename="lease.pdf", storage_key="k", sha256="a" * 64, page_count=4, session_hash=HASH))
        s.add(Run(id=1, document_id=1, status="done", session_hash=HASH))
        s.add(Field(id=10, run_id=1, name="end_date", value_json="2027-03-31", page_no=2,
                    bbox=[1, 2, 3, 4], bboxes=[[1, 2, 3, 4]], confidence=0.99, flags=[]))
        s.add(Field(id=11, run_id=1, name="notice_period_days", value_json=90, page_no=3,
                    bbox=[5, 6, 7, 8], bboxes=[[5, 6, 7, 8], [5, 9, 7, 12]], confidence=0.99, flags=[]))
        s.add(ProposedAction(id=1, run_id=1, tool="create_calendar_event",
                             args_json={"title": "Notice due", "date": "2026-12-17", "description": "d"},
                             source_field_ids=[10, 11], rationale="deadline", status="pending",
                             policy_reason="needs approval"))
        s.add(ProposedAction(id=2, run_id=1, tool="draft_email",
                             args_json={"subject": "Renewal", "body": "Hi", "to": "{{RECIPIENT}}"},
                             source_field_ids=[11], status="pending", policy_reason="draft"))
        s.commit()

    def override():
        with Session() as session:
            yield session

    app.dependency_overrides[get_session] = override
    client = TestClient(app)
    log_in(client, COOKIE)
    yield client, Session
    app.dependency_overrides.clear()


def test_list_actions_includes_sources_with_pages(env):
    client, _ = env
    actions = client.get("/runs/1/actions").json()
    assert [a["tool"] for a in actions] == ["create_calendar_event", "draft_email"]
    first = actions[0]
    assert first["status"] == "pending" and first["result"] is None and first["side_effect"] is True
    assert [(s["name"], s["page_no"]) for s in first["sources"]] == [("end_date", 2), ("notice_period_days", 3)]
    assert first["sources"][1]["bboxes"] == [[5, 6, 7, 8], [5, 9, 7, 12]]


def test_approve_executes_through_the_sandbox_adapter(env):
    client, Session = env
    response = client.post("/actions/1/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["result"]["adapter"] == "sandbox"
    assert body["result"]["response"]["status"] == "created (sandbox)"
    with Session() as s:
        sandbox = s.scalars(select(SandboxAction)).all()
        assert len(sandbox) == 1 and sandbox[0].tool == "create_calendar_event"
        assert sandbox[0].args_json["date"] == "2026-12-17" and sandbox[0].action_id == 1
        assert len(s.scalars(select(ActionResult)).all()) == 1


def test_approving_a_draft_never_sends(env):
    client, Session = env
    body = client.post("/actions/2/approve").json()
    assert body["status"] == "executed"
    assert "not sent" in body["result"]["response"]["status"]
    with Session() as s:
        assert s.scalars(select(SandboxAction)).one().args_json["to"] == "{{RECIPIENT}}"


def test_reject_does_not_execute(env):
    client, Session = env
    body = client.post("/actions/1/reject").json()
    assert body["status"] == "rejected" and body["result"] is None
    with Session() as s:
        assert s.scalars(select(SandboxAction)).all() == []
        assert s.scalars(select(ActionResult)).all() == []


def test_cannot_decide_twice(env):
    client, _ = env
    assert client.post("/actions/1/approve").status_code == 200
    assert client.post("/actions/1/approve").status_code == 409
    assert client.post("/actions/1/reject").status_code == 409
    assert client.post("/actions/2/reject").status_code == 200
    assert client.post("/actions/2/approve").status_code == 409


def test_unknown_action_and_run_are_404(env):
    client, _ = env
    assert client.post("/actions/999/approve").status_code == 404
    assert client.post("/actions/999/reject").status_code == 404
    assert client.get("/runs/999/actions").status_code == 404


def test_fields_endpoint_exposes_per_line_boxes(env):
    client, _ = env
    fields = {f["name"]: f for f in client.get("/runs/1/fields").json()}
    assert fields["notice_period_days"]["bboxes"] == [[5, 6, 7, 8], [5, 9, 7, 12]]
