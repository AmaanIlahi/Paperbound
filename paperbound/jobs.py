"""Background runs: a job queue in Postgres, one in-process worker, and the writer of the run's event stream.

POST /documents saves the upload and queues a job. The worker claims one queued job at a time
(SELECT ... FOR UPDATE SKIP LOCKED) and runs the pipeline. As each step finishes, the matching events are written to
`run_events` with a narration drafted by code (paperbound/narration.py). GET /runs/{id}/events replays those rows and
then follows new ones, so a page that reconnects, or opens a run later, sees the same stream as one that watched it live.

The events have exactly the shapes of the sample replay (paperbound/demo/replay.py), so one renderer handles both.
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from paperbound import clock, usage
from paperbound.agent.executor import execute_action
from paperbound.agent.planner import resolve_placeholders
from paperbound.db.models import Document, Field, Job, Obligation, ProposedAction, Run, RunEvent, Step
from paperbound.db.session import new_session
from paperbound.doctypes import SUPPORTED, UNSUPPORTED_MESSAGE
from paperbound.narration import DEFAULT_ACTION_TEXT, Narrator
from paperbound.pipeline.extract import MODEL
from paperbound.pipeline.parse import ParsedPage
from paperbound.pipeline.runner import make_parse_step, run_pipeline
from paperbound.pipeline.types import StepRecord
from paperbound.presenters import action_json, brief_json, field_json, obligation_json

log = logging.getLogger(__name__)

POLL_SECONDS = 0.5
TERMINAL = ("done", "partial", "failed")


def pipeline_overrides() -> dict:
    """Extra keyword arguments for run_pipeline (extractor, planner, classifier, today). Tests replace this."""
    return {}


def pages_from_document(document: Document) -> list[ParsedPage]:
    return [
        ParsedPage(
            page_no=p.page_no, route=p.route, text=p.text, words=p.words_json, quality_score=p.quality_score,
            width_pt=p.width_pt, height_pt=p.height_pt, image_png=b"",
        )
        for p in document.pages
    ]


# ---------------------------------------------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------------------------------------------

def emit(session: Session, run_id: int, name: str, data: dict) -> int:
    """Append one event and commit, so followers of the stream see it at once. Returns its sequence number."""
    seq = (session.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)) or 0) + 1
    session.add(RunEvent(run_id=run_id, seq=seq, name=name, data=jsonable_encoder(data)))
    session.commit()
    return seq


def live_badge(as_of: str) -> str:
    return f"Your document · live run · as of {as_of}"


def meta_event(document: Document, run: Run, as_of: str) -> dict:
    return {
        "fixture": None, "live": True, "document_id": document.id, "run_id": run.id, "label": document.filename,
        "filename": document.filename, "doc_type": None, "classification": None, "recorded_at": None, "as_of": as_of,
        "model": MODEL, "page_count": document.page_count, "badge": live_badge(as_of),
        "narration": Narrator("document", document.filename, document.page_count, as_of).intro(live=True),
    }


def step_event(seq: int, step: StepRecord, narration: str, status: str | None = None) -> dict:
    return {
        "seq": seq, "stage": step.stage, "status": status or step.status, "summary": step.output_summary,
        "input_summary": step.input_summary, "reason": step.reason, "error": step.error, "model": step.model,
        "tokens": step.tokens, "cost_usd": step.cost_usd, "duration_ms": step.duration_ms,
        "ts_offset_ms": step.ts_offset_ms, "narration": narration,
    }


def action_narration(group_key: str | None, tool: str) -> str:
    return (DEFAULT_ACTION_TEXT.get(group_key or "") or DEFAULT_ACTION_TEXT.get("tool:" + tool) or DEFAULT_ACTION_TEXT["default"])


# ---------------------------------------------------------------------------------------------------------------
# one run
# ---------------------------------------------------------------------------------------------------------------

def _execute(session: Session, job: Job, run: Run, document: Document) -> None:
    today = clock.today()
    as_of = today.isoformat()
    pages = pages_from_document(document)
    parse_step = make_parse_step(pages, job.payload.get("parse_ms", 0), job.payload.get("reused", False))
    narrator = Narrator("document", document.filename, len(pages), as_of)
    seqs: dict[str, int] = {}
    counter = {"n": 0}
    state: dict = {"fields": {}}

    def next_seq() -> int:
        counter["n"] += 1
        return counter["n"]

    def persist_step(seq: int, step: StepRecord) -> None:
        session.add(Step(run_id=run.id, seq=seq, **vars(step)))

    def send(name: str, data: dict) -> None:
        emit(session, run.id, name, data)

    # parse already happened at upload time: report it first
    seq = next_seq()
    send("step", step_event(seq, StepRecord(stage="parse", status="running"), "", "running"))
    persist_step(seq, parse_step)
    send("step", step_event(seq, parse_step, narrator.parse()))

    def on_classified(step: StepRecord, result) -> str:
        nonlocal narrator
        cls = result.classification
        spec = SUPPORTED.get(result.doc_type or "")
        narrator = Narrator(spec.label.lower() if spec else "document", document.filename, len(pages), as_of)
        document.doc_type = result.doc_type
        info = {"method": cls.method, "reason": cls.reason, "scores": cls.scores} if cls else None
        send("meta", {"doc_type": result.doc_type, "classification": info})
        return narrator.classify(info, result.doc_type)

    def on_validated(step: StepRecord, result) -> str:
        rows = [
            Field(run_id=run.id, name=f.name, value_json=f.value, source_quote=f.source_quote, page_no=f.page_no,
                  bbox=f.bbox, bboxes=f.bboxes, confidence=f.confidence, flags=f.flags)
            for f in result.fields
        ]
        session.add_all(rows)
        session.flush()
        state["fields"] = {r.name: r for r in rows}
        for r in rows:
            send("field", {"phase": "extracted", "name": r.name, "value": r.value_json, "page_no": r.page_no,
                           "source_quote": r.source_quote})
        for r in rows:
            send("field", {"phase": "validated", **field_json(r)})
        return narrator.validate([{"value": f.value, "flags": f.flags, "name": f.name} for f in result.fields])

    def on_obligations(step: StepRecord, result) -> str:
        rows = [
            Obligation(run_id=run.id, kind=o.kind, due_date=o.due_date, description=o.description,
                       source_field_ids=[state["fields"][n].id for n in o.source_fields if n in state["fields"]])
            for o in result.obligations
        ]
        session.add_all(rows)
        session.flush()
        state["obligations"] = {"obligations": [obligation_json(o) for o in rows]}
        return narrator.obligations([{"description": o.description} for o in rows])

    def on_impact(step: StepRecord, result) -> str:
        state["brief"] = brief_json(result.brief, state["fields"])
        return narrator.impact(state["brief"]["lines"])

    def on_planner(step: StepRecord, result) -> str:
        actions = []
        for planned in result.plan.actions:
            action = ProposedAction(
                run_id=run.id, tool=planned.tool, args_json=resolve_placeholders(planned.args, run.id),
                source_field_ids=[state["fields"][n].id for n in planned.source_fields if n in state["fields"]],
                rationale=planned.rationale, status="pending", policy_reason=planned.policy_reason,
                group_key=planned.group_key, group_mode=planned.group_mode,
            )
            session.add(action)
            session.flush()
            if planned.policy_status == "approved":  # auto-approval is off by default
                action.status = "approved"
                execute_action(session, action)
            actions.append(action)
        state["actions"] = actions
        return narrator.planner(len(actions), step.reason)

    handlers = {"classify": on_classified, "validate": on_validated, "obligations": on_obligations,
                "impact": on_impact, "planner": on_planner}

    def after_step(stage: str, result) -> None:
        """Events that follow a finished step: the data it produced."""
        if stage == "obligations" and "obligations" in state:
            send("obligations", state.pop("obligations"))
        elif stage == "impact" and "brief" in state:
            send("brief", state.pop("brief"))
        elif stage == "planner":
            by_id = {r.id: r for r in state["fields"].values()}
            actions = state.get("actions", [])
            for action in actions:
                send("action", {**action_json(session, action, by_id),
                                "narration": action_narration(action.group_key, action.tool)})
            if actions:
                gate = StepRecord(stage="gate", status="done", output_summary="all actions pending approval")
                send("step", step_event(next_seq(), gate, narrator.gate({a.group_key for a in actions if a.group_key}, live=True)))

    def observer(event: str, stage: str, step: StepRecord | None, result) -> None:
        if event == "start":
            seqs[stage] = next_seq()
            send("step", step_event(seqs[stage], StepRecord(stage=stage, status="running"), "", "running"))
            return
        seq = seqs.get(stage) or next_seq()
        if step.status == "failed":
            text = narrator.failed(stage, step.error or "unknown error")
        elif step.status == "skipped":
            text = narrator.no_planner() if stage == "planner" and not result.obligations else narrator.skipped(stage, step.reason)
        elif stage == "extract":
            text = narrator.extract(result.extracted_present, {"tokens": step.tokens, "cost_usd": step.cost_usd})
        elif stage in handlers:
            text = handlers[stage](step, result)
        else:
            text = ""
        persist_step(seq, step)
        send("step", step_event(seq, step, text))
        if step.status == "done":
            after_step(stage, result)

    earlier = session.scalars(
        select(Obligation).join(Run, Obligation.run_id == Run.id)
        .where(Run.document_id == document.id, Run.id != run.id, Run.session_hash == run.session_hash)
    )
    existing = [
        {"kind": o.kind, "due_date": o.due_date.isoformat() if o.due_date else None, "description": o.description}
        for o in earlier
    ]
    result = run_pipeline(pages, parse_step, existing_obligations=existing, observer=observer, **pipeline_overrides())

    run.status = result.status
    run.cost_usd = result.cost_usd
    run.finished_at = datetime.now(timezone.utc)
    usage.record_spend(session, result.cost_usd, datetime.now(timezone.utc))
    job.status, job.finished_at = "done", datetime.now(timezone.utc)
    calls = sum(1 for s in result.steps if s.model) + max(0, (result.plan.api_calls if result.plan else 1) - 1)
    send("done", {
        "status": result.status, "doc_type": result.doc_type, "cost_usd": result.cost_usd, "tokens": result.tokens,
        "message": UNSUPPORTED_MESSAGE if result.message else None,
        "narration": narrator.outro(calls, result.tokens, result.cost_usd, live=True),
    })


def _fail(session: Session, job_id: int, run_id: int, error: str) -> None:
    job, run = session.get(Job, job_id), session.get(Run, run_id)
    job.status, job.error, job.finished_at = "failed", error[:2000], datetime.now(timezone.utc)
    run.status, run.finished_at = "failed", datetime.now(timezone.utc)
    emit(session, run_id, "done", {
        "status": "failed", "doc_type": None, "cost_usd": run.cost_usd, "tokens": 0,
        "message": "Something went wrong while processing this document, so the run stopped. Nothing was proposed.",
        "narration": "The run failed before it could finish. Whatever completed is shown; no actions were created after the failure.",
    })


def run_job(job_id: int, make_session=None) -> None:
    """Run one claimed job to the end. Never raises: a failure becomes a `done` event with status failed."""
    with (make_session or new_session)() as session:
        job = session.get(Job, job_id)
        run = session.get(Run, job.run_id)
        document = session.get(Document, run.document_id)
        try:
            _execute(session, job, run, document)
        except Exception as exc:
            log.exception("job %s failed", job_id)
            session.rollback()
            _fail(session, job_id, run.id, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------------------------------------------
# the queue and the worker
# ---------------------------------------------------------------------------------------------------------------

def next_job_query():
    return select(Job).where(Job.status == "queued").order_by(Job.id).limit(1).with_for_update(skip_locked=True)


def claim_next(make_session=None) -> int | None:
    """Take the oldest queued job. FOR UPDATE SKIP LOCKED lets several workers share the table without clashing
    (SQLite, used by tests, ignores it)."""
    with (make_session or new_session)() as session:
        job = session.scalar(next_job_query())
        if job is None:
            return None
        job.status, job.started_at = "running", datetime.now(timezone.utc)
        run = session.get(Run, job.run_id)
        run.status = "running"
        session.commit()
        return job.id


def process_one(make_session=None) -> bool:
    """Claim and run one job. True if there was one."""
    job_id = claim_next(make_session)
    if job_id is None:
        return False
    run_job(job_id, make_session)
    return True


def recover_interrupted(make_session=None) -> int:
    """Jobs left 'running' by a restart will never finish: end them with a failed run instead of a stream that hangs."""
    with (make_session or new_session)() as session:
        stuck = session.scalars(select(Job).where(Job.status == "running")).all()
        for job in stuck:
            _fail(session, job.id, job.run_id, "interrupted by a restart")
        return len(stuck)


async def worker_loop(make_session=None) -> None:
    """The one background worker, started in the FastAPI lifespan. Blocking work runs in a thread."""
    await asyncio.to_thread(recover_interrupted, make_session)
    while True:
        try:
            worked = await asyncio.to_thread(process_one, make_session)
        except Exception:
            log.exception("worker iteration failed")
            worked = False
        if not worked:
            await asyncio.sleep(POLL_SECONDS)
