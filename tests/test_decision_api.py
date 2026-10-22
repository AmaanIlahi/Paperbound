import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from paperbound.api.main import app
from paperbound.db.models import Base, Document, Field, ProposedAction, Run, SandboxAction
from paperbound.db.session import get_session
from tests.helpers import log_in, new_visitor

FIELDS = [
    # id, name, value, page, flags
    (10, "end_date", "2027-03-31", 2, []),
    (11, "notice_period_days", 90, 3, []),
    (12, "current_rent", 9017.65, 2, []),
    (13, "holdover_rate_percent", 150.0, 3, []),
    (14, "rent_escalation_percent", 3.0, 2, []),
    (15, "notice_method", "certified mail", 3, []),
    (16, "notice_section", "Section 11", 3, []),
    (17, "security_deposit", 17000.0, 2, ["conflicting_values"]),  # blocked: must not appear in the brief
]


COOKIE, HASH = new_visitor()


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as s:
        s.add(Document(id=1, filename="lease.pdf", storage_key="k", sha256="a" * 64, page_count=4, session_hash=HASH))
        s.add(Run(id=1, document_id=1, status="done", session_hash=HASH))
        s.add(Run(id=2, document_id=1, status="done", session_hash=HASH))
        for fid, name, value, page, flags in FIELDS:
            s.add(Field(id=fid, run_id=1, name=name, value_json=value, page_no=page, bbox=[1, 2, 3, 4],
                        bboxes=[[1, 2, 3, 4], [1, 5, 3, 8]], confidence=0.99, flags=flags))
        for aid, date in ((1, "2026-11-01"), (2, "2026-12-01"), (3, "2026-12-17")):
            s.add(ProposedAction(id=aid, run_id=1, tool="create_calendar_event",
                                 args_json={"title": "t", "date": date, "description": "d"},
                                 source_field_ids=[10, 11], status="pending", group_key="reminders", group_mode="bundle"))
        s.add(ProposedAction(id=4, run_id=1, tool="draft_email", args_json={"subject": "Renew", "body": "b", "to": "{{RECIPIENT}}"},
                             source_field_ids=[11, 16], status="pending", group_key="notice_choice", group_mode="exclusive",
                             policy_reason="draft"))
        s.add(ProposedAction(id=5, run_id=1, tool="draft_email", args_json={"subject": "Terminate", "body": "b", "to": "{{RECIPIENT}}"},
                             source_field_ids=[11, 16], status="pending", group_key="notice_choice", group_mode="exclusive",
                             policy_reason="draft"))
        s.add(ProposedAction(id=6, run_id=1, tool="upsert_record", args_json={"record_type": "obligation", "key": "k", "data": {}},
                             source_field_ids=[10], status="pending"))
        # same group key in another run: must never be touched
        s.add(ProposedAction(id=7, run_id=2, tool="draft_email", args_json={"subject": "Renew", "body": "b", "to": "{{RECIPIENT}}"},
                             source_field_ids=[], status="pending", group_key="notice_choice", group_mode="exclusive"))
        s.commit()

    def override():
        with Session() as session:
            yield session

    app.dependency_overrides[get_session] = override
    client = TestClient(app)
    log_in(client, COOKIE)
    yield client, Session
    app.dependency_overrides.clear()


def statuses(Session):
    with Session() as s:
        return {a.id: a.status for a in s.scalars(select(ProposedAction))}


def test_brief_endpoint_returns_numbers_with_source_field_ids_and_pages(env):
    client, _ = env
    body = client.get("/runs/1/brief", params={"today": "2026-10-22"}).json()
    lines = {ln["key"]: ln for ln in body["lines"]}
    assert lines["deadline"]["text"] == "December 31, 2026" and lines["days_remaining"]["value"] == 70
    assert lines["holdover_rent"]["value"] == 13526.48 and lines["holdover_extra"]["value"] == 4508.83
    rent = lines["holdover_rent"]
    assert [(s["field_id"], s["name"], s["page_no"]) for s in rent["sources"]] == [
        (12, "current_rent", 2), (13, "holdover_rate_percent", 3),
    ]
    assert rent["sources"][1]["bboxes"] == [[1, 2, 3, 4], [1, 5, 3, 8]]
    assert [s["page_no"] for s in lines["deadline"]["sources"]] == [2, 3]


def test_brief_skips_blocked_fields_and_says_why(env):
    client, _ = env
    body = client.get("/runs/1/brief").json()
    assert "security_deposit" not in {ln["key"] for ln in body["lines"]}
    reason = next(s["reason"] for s in body["skipped"] if s["key"] == "security_deposit")
    assert "conflicting_values" in reason


def test_brief_of_unknown_run_is_404(env):
    assert env[0].get("/runs/999/brief").status_code == 404


def test_actions_expose_their_group(env):
    client, _ = env
    actions = {a["id"]: a for a in client.get("/runs/1/actions").json()}
    assert actions[1]["group_key"] == "reminders" and actions[1]["group_mode"] == "bundle"
    assert actions[4]["group_mode"] == "exclusive" and actions[6]["group_key"] is None


def test_approving_one_email_auto_rejects_the_other(env):
    client, Session = env
    assert client.post("/actions/4/approve").status_code == 200
    state = statuses(Session)
    assert state[4] == "executed" and state[5] == "rejected"
    assert state[7] == "pending"  # another run is untouched
    assert state[1] == state[2] == state[3] == state[6] == "pending"  # other groups too
    other = client.get("/runs/1/actions").json()[4]
    assert "Auto-rejected" in other["policy_reason"] and "action 4" in other["policy_reason"]
    assert client.post("/actions/5/approve").status_code == 409  # the choice is made
    with Session() as s:
        assert [x.tool for x in s.scalars(select(SandboxAction))] == ["draft_email"]


def test_rejecting_one_email_leaves_the_other_open(env):
    client, Session = env
    assert client.post("/actions/4/reject").status_code == 200
    assert statuses(Session)[5] == "pending"
    assert client.post("/actions/5/approve").status_code == 200
    assert statuses(Session)[5] == "executed"


def test_reminders_are_a_bundle_and_approve_independently(env):
    client, Session = env
    assert client.post("/actions/1/approve").status_code == 200
    state = statuses(Session)
    assert state[1] == "executed" and state[2] == state[3] == "pending"
    assert client.post("/actions/2/approve").status_code == 200 and client.post("/actions/3/reject").status_code == 200
    assert {k: v for k, v in statuses(Session).items() if k in (1, 2, 3)} == {1: "executed", 2: "executed", 3: "rejected"}
