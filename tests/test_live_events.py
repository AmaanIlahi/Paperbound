"""The job queue, the worker, and the run event stream: same events as the sample replay, replayable after the fact."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from paperbound import jobs
from paperbound.api import routes
from paperbound.api.main import app, lifespan
from paperbound.db.models import Document, Job, ProposedAction, Run, RunEvent, Step
from paperbound.demo import replay
from paperbound.pipeline.classify import Classification
from tests.lease_fakes import TODAY
from tests.live import lease_pipeline, live  # noqa: F401
from tests.test_routing import recipe_pdf


def names(events):
    return [name for _, name, _ in events]


def key_sets(events):
    """{(event, phase or stage or ''): union of the keys it carries}"""
    out = {}
    for _, name, data in events:
        label = (name, data.get("phase") or data.get("stage") or "")
        out.setdefault(label, set()).update(data)
    return out


# ---- the queue and the worker ----

def test_the_claim_query_uses_for_update_skip_locked_on_postgres():
    sql = str(jobs.next_job_query().compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql and "ORDER BY jobs.id" in sql and "jobs.status" in sql


def test_jobs_are_claimed_once_and_in_order(live):
    first, second = live.upload().json(), live.upload(client=live.visitor()).json()
    claimed = [jobs.claim_next(live.Session), jobs.claim_next(live.Session), jobs.claim_next(live.Session)]
    assert claimed[2] is None and claimed[0] != claimed[1]
    with live.Session() as s:
        by_run = {j.run_id: j for j in s.scalars(select(Job))}
        assert by_run[first["run_id"]].id == claimed[0] and by_run[second["run_id"]].id == claimed[1]
        assert {j.status for j in by_run.values()} == {"running"}
        assert s.get(Run, first["run_id"]).status == "running"


def test_the_worker_runs_each_queued_job_once(live):
    ids = [live.upload(client=live.visitor()).json()["run_id"] for _ in range(3)]
    assert live.work() == 3 and live.work() == 0
    with live.Session() as s:
        assert [s.get(Run, i).status for i in ids] == ["done"] * 3 and {j.status for j in s.scalars(select(Job))} == {"done"}
        assert len(s.scalars(select(ProposedAction)).all()) == 18  # 6 per run, none doubled


def test_a_crash_inside_the_pipeline_becomes_a_failed_run_not_a_dead_worker(live):
    live.use_pipeline(lambda: {"no_such_option": 1})  # run_pipeline raises TypeError outside any stage
    body = live.upload_and_run()
    run = live.client.get(f"/runs/{body['run_id']}").json()
    assert run["status"] == "failed"
    done = live.events(body["run_id"])[-1]
    assert done[1] == "done" and done[2]["status"] == "failed" and "went wrong" in done[2]["message"]
    with live.Session() as s:
        job = s.scalar(select(Job))
        assert job.status == "failed" and "TypeError" in job.error
    live.use_pipeline(lease_pipeline())  # and the next upload is processed normally
    assert live.client.get(f"/runs/{live.upload_and_run(client=live.visitor())['run_id']}").status_code == 404
    assert live.work() == 0


def test_a_failing_stage_gives_a_partial_run_with_the_failure_narrated(live):
    def boom(pages):
        raise RuntimeError("openai is down")

    live.use_pipeline(lease_pipeline(extractor=boom))
    run_id = live.upload_and_run()["run_id"]
    events = live.events(run_id)
    steps = {d["stage"]: d for _, n, d in events if n == "step" and d["status"] != "running"}
    assert steps["extract"]["status"] == "failed" and "openai is down" in steps["extract"]["narration"]
    assert steps["validate"]["status"] == "skipped" and steps["planner"]["status"] == "skipped"
    assert events[-1][2]["status"] == "partial" and "field" not in names(events)
    assert live.client.get(f"/runs/{run_id}").json()["status"] == "partial"


def test_a_run_interrupted_by_a_restart_is_ended_cleanly(live):
    body = live.upload().json()
    assert jobs.claim_next(live.Session) is not None  # claimed, then the process died
    assert jobs.recover_interrupted(live.Session) == 1
    events = live.events(body["run_id"])
    assert events[-1][1] == "done" and events[-1][2]["status"] == "failed"
    with live.Session() as s:
        assert s.scalar(select(Job)).error == "interrupted by a restart" and s.get(Run, body["run_id"]).status == "failed"
    assert jobs.recover_interrupted(live.Session) == 0


def test_the_worker_starts_with_the_app_only_when_uploads_are_possible(live, monkeypatch):
    started = []

    async def fake_worker(make_session=None):
        started.append(True)
        await asyncio.sleep(3600)

    monkeypatch.setattr(jobs, "worker_loop", fake_worker)

    async def enter():
        async with lifespan(app):
            await asyncio.sleep(0.05)

    asyncio.run(enter())
    assert started == [True]
    monkeypatch.setattr("paperbound.config.OPENAI_API_KEY", "")
    asyncio.run(enter())
    assert started == [True]  # no key, no worker


# ---- the event stream ----

def test_a_live_run_streams_the_demo_event_types_in_the_demo_order(live):
    run_id = live.upload_and_run()["run_id"]
    events = live.events(run_id)
    demo = replay.build_events(replay.load_snapshot("lease"))
    assert set(names(events)) == {n for _, n, _ in demo} == {"meta", "step", "field", "obligations", "brief", "action", "done"}

    def stage_order(items):
        return [d["stage"] for n, d in items if n == "step" and d["status"] != "running"]

    assert stage_order((n, d) for _, n, d in events) == stage_order((n, d) for _, n, d in demo)
    assert stage_order((n, d) for _, n, d in events) == ["parse", "classify", "extract", "validate", "obligations", "impact", "planner", "gate"]
    running = [d["stage"] for _, n, d in events if n == "step" and d["status"] == "running"]
    assert running == ["parse", "classify", "extract", "validate", "obligations", "impact", "planner"]
    assert names(events)[0] == "meta" and names(events)[-1] == "done"
    assert [seq for seq, _, _ in events] == list(range(1, len(events) + 1))  # ids are the replay cursor


def test_every_event_carries_the_same_keys_as_its_demo_twin(live):
    live_keys = key_sets(live.events(live.upload_and_run()["run_id"]))
    demo_keys = key_sets([(0, n, d) for _, n, d in replay.build_events(replay.load_snapshot("lease"))])
    assert set(live_keys) == set(demo_keys)
    extras = {"meta": {"live", "document_id", "run_id"}, "action": {"run_id"}}  # ids that only a stored run has
    for label, keys in demo_keys.items():
        assert keys <= live_keys[label], (label, keys - live_keys[label])
        assert live_keys[label] - keys <= extras.get(label[0], set()), (label, live_keys[label] - keys)


def test_values_have_the_demo_types_so_one_renderer_fits_both(live):
    events = live.events(live.upload_and_run()["run_id"])
    by_name = {}
    for _, n, d in events:
        by_name.setdefault(n, []).append(d)
    brief = by_name["brief"][0]
    assert {"lines", "skipped"} == set(brief) and all({"key", "label", "text", "value", "sources"} <= set(ln) for ln in brief["lines"])
    assert all({"field_id", "name", "page_no", "bboxes"} == set(s) for ln in brief["lines"] for s in ln["sources"])
    assert by_name["obligations"][0]["obligations"][0]["due_date"] == "2026-12-31"
    action = by_name["action"][0]
    assert {"id", "tool", "args", "status", "group_key", "group_mode", "sources", "narration", "result", "rationale"} <= set(action)
    validated = [d for d in by_name["field"] if d["phase"] == "validated"]
    assert all({"id", "name", "value", "page_no", "bboxes", "confidence", "flags", "source_quote"} <= set(d) for d in validated)
    done = by_name["done"][0]
    assert done["status"] == "done" and done["message"] is None and "model call" in done["narration"]


def test_live_narration_is_written_by_code_for_every_step_and_names_what_happened(live):
    events = live.events(live.upload_and_run()["run_id"])
    text = {d["stage"]: d["narration"] for _, n, d in events if n == "step" and d["status"] != "running"}
    assert all(len(t) > 40 for t in text.values()) and set(text) >= {"parse", "classify", "extract", "validate", "gate"}
    assert "no model" in text["parse"].lower() and "14 of 14 fields" in text["validate"]
    assert "gpt-4o-mini" in text["extract"] and "commercial lease" in text["classify"]
    assert "December 31, 2026" in text["impact"] and "not the model" in text["gate"]
    actions = [d for _, n, d in events if n == "action"]
    assert any("Staged reminders" in a["narration"] for a in actions) and any("tracker record" in a["narration"] for a in actions)


def test_unsupported_documents_stop_after_classification_with_a_message(live):
    live.use_pipeline(lambda: {"today": TODAY, "classifier": lambda pages: Classification("other", "llm", "A pancake recipe.")})
    events = live.events(live.upload_and_run(recipe_pdf(), "recipe.pdf")["run_id"])
    done = events[-1][2]
    assert done["status"] == "done" and done["doc_type"] == "other" and done["message"] == "document type not supported yet"
    assert not {"field", "obligations", "brief", "action"} & set(names(events))
    metas = [d for _, n, d in events if n == "meta"]
    assert metas[-1]["doc_type"] == "other" and metas[-1]["classification"]["method"] == "llm"
    classify = next(d for _, n, d in events if n == "step" and d["stage"] == "classify" and d["status"] == "done")
    assert "stops here instead of guessing" in classify["narration"]


def test_meta_arrives_before_the_worker_and_again_when_the_type_is_known(live):
    body = live.upload().json()
    queued = live.first_events(body["run_id"], 1)
    assert queued[0][2]["doc_type"] is None and queued[0][2]["page_count"] == 4 and queued[0][2]["live"] is True
    assert queued[0][2]["badge"].startswith("Your document · live run · as of ")
    live.work()
    metas = [d for _, n, d in live.events(body["run_id"]) if n == "meta"]
    assert [m["doc_type"] for m in metas] == [None, "lease"] and metas[1]["classification"]["method"] == "heuristic"


# ---- reconnecting ----

def test_after_and_last_event_id_replay_only_what_was_missed(live):
    run_id = live.upload_and_run()["run_id"]
    everything = live.events(run_id)
    cut = everything[20][0]
    assert live.events(run_id, after=cut) == everything[20 + 1:]
    header = live.client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": str(cut)})
    from tests.live import parse_sse
    assert parse_sse(header.text) == everything[21:]
    assert live.events(run_id, after=everything[-1][0]) == []  # caught up on a finished run: nothing left, stream ends


def test_a_stream_opened_early_follows_the_worker_to_the_end(live):
    run_id = live.upload().json()["run_id"]

    async def follow():
        gen = routes.stream_events(lambda: live.Session(), run_id, 0, poll=0.01)
        assert await gen.__anext__() == "retry: 1500\n\n"
        assert "event: meta" in await gen.__anext__()
        assert await asyncio.to_thread(live.work) == 1  # the worker (a thread, as in the app) finishes while the stream is parked
        frames = [f async for f in gen]
        return "".join(frames)

    text = asyncio.run(follow())
    from tests.live import parse_sse
    events = parse_sse(text)
    assert events[0][0] == 2 and events[-1][1] == "done" and "event: action" in text  # continued after meta, id 1


def test_replaying_a_run_shows_the_current_state_of_its_actions(live):
    run_id = live.upload_and_run()["run_id"]
    actions = live.client.get(f"/runs/{run_id}/actions").json()
    live.client.post(f"/actions/{actions[0]['id']}/approve")
    live.client.post(f"/actions/{actions[3]['id']}/approve")  # approving one notice rejects the other
    replayed = [d for _, n, d in live.events(run_id) if n == "action"]
    status = {a["id"]: a["status"] for a in replayed}
    assert status[actions[0]["id"]] == "executed" and status[actions[3]["id"]] == "executed" and status[actions[4]["id"]] == "rejected"
    first = next(a for a in replayed if a["id"] == actions[0]["id"])
    assert first["result"]["adapter"] == "sandbox" and first["narration"]  # the narration from the event is kept
    assert "Auto-rejected" in next(a for a in replayed if a["id"] == actions[4]["id"])["policy_reason"]


def test_a_finished_run_whose_events_are_gone_ends_the_stream_instead_of_hanging(live):
    run_id = live.upload_and_run()["run_id"]
    with live.Session() as s:
        for row in s.scalars(select(RunEvent)):
            s.delete(row)
        s.commit()
    assert live.events(run_id) == []


def test_steps_are_stored_as_the_run_goes_so_the_rest_endpoints_still_work(live):
    run_id = live.upload_and_run()["run_id"]
    with live.Session() as s:
        stored = [(x.seq, x.stage) for x in s.scalars(select(Step).where(Step.run_id == run_id).order_by(Step.seq))]
        assert s.scalar(select(Document)).doc_type == "lease"
    events = live.events(run_id)
    event_seqs = {d["stage"]: d["seq"] for _, n, d in events if n == "step" and d["status"] != "running"}
    assert stored == [(event_seqs[stage], stage) for _, stage in stored]  # a step's number is the same in both places
