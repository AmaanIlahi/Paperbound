"""POST /documents on the real lease fixture with a fake LLM: the upload returns at once, the worker runs the job, and the
saved run has the same data as before (steps, fields, staged actions with groups, resolved links)."""

from sqlalchemy import select

from paperbound.db.models import Job, ProposedAction, Run
from tests.lease_fakes import LEASE, QUOTES, TODAY, fake_extractor, fake_planner  # noqa: F401  (re-exported for other tests)
from tests.live import live  # noqa: F401


def test_upload_returns_immediately_with_a_queued_run_and_does_no_pipeline_work(live):
    response = live.upload()
    body = response.json()
    assert response.status_code == 200 and body["status"] == "queued" and body["page_count"] == 4
    assert set(body) == {"document_id", "run_id", "status", "page_count", "reused"}
    with live.Session() as s:
        assert s.get(Run, body["run_id"]).status == "queued" and s.scalar(select(Job)).status == "queued"
        assert s.scalars(select(ProposedAction)).all() == []  # nothing has been extracted or planned yet
    # the stream already has its first event, so the page can show the document while the run waits in the queue
    first = live.first_events(body["run_id"], 1)[0]
    assert first[1] == "meta" and first[2]["document_id"] == body["document_id"] and first[2]["page_count"] == 4


def test_the_worker_persists_the_staged_plan_with_groups_and_resolved_links(live):
    run_id = live.upload_and_run()["run_id"]
    actions = live.client.get(f"/runs/{run_id}/actions").json()
    assert [a["tool"] for a in actions] == ["create_calendar_event"] * 3 + ["draft_email"] * 2 + ["upsert_record"]
    assert {a["group_key"] for a in actions[:3]} == {"reminders"} and {a["group_mode"] for a in actions[3:5]} == {"exclusive"}
    assert all(a["status"] == "pending" for a in actions)
    description = actions[0]["args"]["description"]
    assert f"/#run={run_id}&page=3" in description and "{{RUN_ID}}" not in description
    pages = {s["name"]: s["page_no"] for s in actions[0]["sources"]}
    assert pages["end_date"] == 2 and pages["notice_period_days"] == 3 and pages["notice_section"] == 3
    assert all(s["bboxes"] for s in actions[0]["sources"])


def test_run_endpoint_and_brief_work_for_a_finished_run(live):
    run_id = live.upload_and_run()["run_id"]
    run = live.client.get(f"/runs/{run_id}").json()
    assert run["page_count"] == 4 and run["status"] == "done" and run["doc_type"] == "lease"
    steps = [s["stage"] for s in live.client.get(f"/runs/{run_id}/steps").json()]
    assert steps == ["parse", "classify", "extract", "validate", "obligations", "impact", "planner"]
    brief = live.client.get(f"/runs/{run_id}/brief", params={"today": "2026-10-22"}).json()
    lines = {ln["key"]: ln for ln in brief["lines"]}
    assert lines["holdover_rent"]["value"] == 13526.48 and brief["skipped"] == []
    assert all(ln["sources"] for ln in brief["lines"])


def test_choosing_one_notice_through_the_saved_run_rejects_the_other(live):
    run_id = live.upload_and_run()["run_id"]
    actions = live.client.get(f"/runs/{run_id}/actions").json()
    renew, terminate = [a for a in actions if a["tool"] == "draft_email"]
    assert live.client.post(f"/actions/{renew['id']}/approve").json()["status"] == "executed"
    assert live.client.post(f"/actions/{terminate['id']}/approve").status_code == 409
    with live.Session() as s:
        by_id = {a.id: a.status for a in s.scalars(select(ProposedAction))}
    assert by_id[terminate["id"]] == "rejected"
    assert sum(1 for a in actions if a["tool"] == "create_calendar_event" and by_id[a["id"]] == "pending") == 3


def test_a_finished_job_is_marked_done_and_the_run_has_cost_and_time(live):
    body = live.upload_and_run()
    with live.Session() as s:
        job = s.scalar(select(Job))
        run = s.get(Run, body["run_id"])
        assert job.status == "done" and job.started_at and job.finished_at and run.finished_at and run.status == "done"
