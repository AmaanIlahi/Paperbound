import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from paperbound import config, deploy, jobs, usage
from paperbound.agent.executor import execute_action
from paperbound.db.models import (
    Document,
    Field,
    Job,
    Obligation,
    Page,
    ProposedAction,
    Run,
    RunEvent,
    Step,
)
from paperbound.db.session import get_session, session_factory
from paperbound.doctypes import UNSUPPORTED_MESSAGE, spec_for
from paperbound.pipeline.parse import parse_pdf
from paperbound.pipeline.types import ValidatedField
from paperbound.presenters import action_json, brief_json, field_json, obligation_json, step_json
from paperbound.sessions import current_session
from paperbound.storage import Storage, get_storage
from paperbound import clock

log = logging.getLogger(__name__)
router = APIRouter()

STREAM_POLL_SECONDS = 0.4
STREAM_HEARTBEAT_SECONDS = 15.0
_FILE_KEY = re.compile(r"^documents/([0-9a-f]{64})/(?:source\.pdf|pages/\d+\.png)$")


# ---------------------------------------------------------------------------------------------------------------
# ownership: a run, its document and everything under it belong to the session that uploaded it. Anyone else,
# including a visitor with no cookie, gets the same 404 as for an id that does not exist.
# ---------------------------------------------------------------------------------------------------------------

def owned_run(session: Session, run_id: int, caller: str) -> Run:
    run = session.get(Run, run_id)
    if run is None or run.session_hash is None or run.session_hash != caller:
        raise HTTPException(404, "Run not found")
    return run


def owned_document(session: Session, document_id: int, caller: str) -> Document:
    document = session.get(Document, document_id)
    owns = document is not None and session.scalar(
        select(Run.id).where(Run.document_id == document_id, Run.session_hash == caller).limit(1)
    )
    if not owns:
        raise HTTPException(404, "Document not found")
    return document


def owned_action(session: Session, action_id: int, caller: str) -> ProposedAction:
    action = session.get(ProposedAction, action_id)
    if action is None:
        raise HTTPException(404, "Action not found")
    try:
        owned_run(session, action.run_id, caller)
    except HTTPException:
        raise HTTPException(404, "Action not found") from None
    return action


# ---------------------------------------------------------------------------------------------------------------
# config and upload guards
# ---------------------------------------------------------------------------------------------------------------

def client_ip(request: Request) -> str:
    """The address the rate limit is keyed on. Fly's proxy sets Fly-Client-IP; otherwise the socket peer."""
    return request.headers.get("fly-client-ip") or (request.client.host if request.client else "unknown")


def require_uploads() -> None:
    """First dependency of the upload route, so a deployment without a database or key answers 503 straight away."""
    reason = deploy.unavailable_reason()
    if reason:
        raise HTTPException(503, f"Uploads are unavailable: {reason} Try a sample instead.")


def restore_files(storage: Storage, document: Document, data: bytes) -> bool:
    """A document is shared by its sha256, but its files live in whichever storage saved them. The database can be
    reached from a place whose storage never got them (a local run against the same database, a restored volume), so a
    re-upload writes back any file the stored pages point to that is missing. Returns True if it wrote anything."""
    keys = [document.storage_key, *(p.image_key for p in document.pages)]
    if all(storage.exists(k) for k in keys):
        return False
    pages = {p.page_no: p for p in parse_pdf(data)}
    storage.save(document.storage_key, data)
    for stored in document.pages:
        if stored.page_no in pages and not storage.exists(stored.image_key):
            storage.save(stored.image_key, pages[stored.page_no].image_png)
    return True


def guard_upload(request: Request, session: Session, storage: Storage, now: datetime) -> None:
    """Everything a public upload must pass before any work is done. Raises HTTPException."""
    try:
        usage.cleanup_expired(session, storage, now)
    except OperationalError as exc:  # the database is unreachable: say so politely instead of a 500
        log.error("database unreachable at upload time: %s", exc.__class__.__name__)
        raise HTTPException(503, f"{deploy.DB_DOWN}.") from exc
    hashed = usage.ip_hash(client_ip(request))
    limit = deploy.uploads_per_ip_per_hour()
    if usage.uploads_last_hour(session, hashed, now) >= limit:
        raise HTTPException(429, f"Upload limit reached: {limit} per hour per address. Try again later, or try a sample.")
    usage.record_upload(session, hashed, now)  # counted even if the file is rejected below
    session.commit()
    budget = deploy.daily_llm_budget_usd()
    if usage.committed_today(session, now, deploy.run_cost_estimate_usd()) >= budget:
        raise HTTPException(429, f"The daily limit is reached (LLM budget ${budget:.2f}). Try again tomorrow (UTC), or try a sample.")


@router.get("/config", include_in_schema=False)
def public_config(factory: Callable = Depends(session_factory)):
    exhausted = database_down = False
    if deploy.uploads_enabled():
        try:
            with factory() as session:
                exhausted = usage.committed_today(
                    session, datetime.now(timezone.utc), deploy.run_cost_estimate_usd()
                ) >= deploy.daily_llm_budget_usd()
        except Exception:  # the page must still load; it offers samples only while the database is unreachable
            log.exception("could not reach the database for /config")
            database_down = True
    return deploy.public_settings(exhausted, database_down)


# ---------------------------------------------------------------------------------------------------------------
# upload: save, queue, return at once. The worker does the rest (paperbound/jobs.py).
# ---------------------------------------------------------------------------------------------------------------

@router.post("/documents", dependencies=[Depends(require_uploads)])
def upload_document(
    request: Request,
    response: Response,
    file: UploadFile,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    caller: str = Depends(current_session),
):
    now = datetime.now(timezone.utc)
    guard_upload(request, session, storage, now)
    limit = deploy.max_upload_bytes()
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File is larger than {deploy.max_upload_bytes() / (1024 * 1024):g} MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Not a PDF file")

    sha256 = hashlib.sha256(data).hexdigest()
    expires = now + timedelta(hours=deploy.upload_ttl_hours())
    document = session.scalar(select(Document).where(Document.sha256 == sha256))
    reused = document is not None
    started = time.perf_counter()
    if document is None:
        try:
            pages = parse_pdf(data)
        except Exception as exc:
            raise HTTPException(400, f"Could not parse PDF: {exc}") from exc
        if len(pages) > deploy.max_pages():
            raise HTTPException(413, f"Document has {len(pages)} pages; the limit is {deploy.max_pages()}.")
        storage.save(f"documents/{sha256}/source.pdf", data)
        document = Document(
            filename=file.filename or "document.pdf", storage_key=f"documents/{sha256}/source.pdf", sha256=sha256,
            page_count=len(pages), expires_at=expires, session_hash=caller,
        )
        for p in pages:
            image_key = f"documents/{sha256}/pages/{p.page_no}.png"
            storage.save(image_key, p.image_png)
            document.pages.append(Page(
                page_no=p.page_no, route=p.route, text=p.text, words_json=p.words, quality_score=p.quality_score,
                image_key=image_key, width_pt=p.width_pt, height_pt=p.height_pt,
            ))
        session.add(document)
        try:
            session.flush()
        except IntegrityError:  # the same file was uploaded a moment ago by someone else: share that document
            session.rollback()
            document = session.scalar(select(Document).where(Document.sha256 == sha256))
            reused = True
    if reused:
        restore_files(storage, document, data)
    if reused and document.expires_at is not None:  # a re-upload keeps an expiring document alive
        document.expires_at = expires
    parse_ms = int((time.perf_counter() - started) * 1000)

    run = Run(document_id=document.id, status="queued", session_hash=caller)
    session.add(run)
    session.flush()
    session.add(Job(run_id=run.id, status="queued", payload={"parse_ms": parse_ms, "reused": reused}))
    jobs.emit(session, run.id, "meta", jobs.meta_event(document, run, clock.today().isoformat()))
    return {
        "document_id": document.id,
        "run_id": run.id,
        "status": run.status,
        "page_count": document.page_count,
        "reused": reused,
    }


@router.get("/documents/{document_id}/pages/{page_no}")
def get_page(
    document_id: int,
    page_no: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    caller: str = Depends(current_session),
):
    owned_document(session, document_id, caller)
    page = session.scalar(select(Page).where(Page.document_id == document_id, Page.page_no == page_no))
    if page is None:
        raise HTTPException(404, "Page not found")
    return {
        "document_id": document_id,
        "page_no": page.page_no,
        "route": page.route,
        "quality_score": page.quality_score,
        "image_url": storage.url(page.image_key),
        "width_pt": page.width_pt,
        "height_pt": page.height_pt,
        "words": page.words_json,
    }


# ---------------------------------------------------------------------------------------------------------------
# reading a run
# ---------------------------------------------------------------------------------------------------------------

@router.get("/runs/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session), caller: str = Depends(current_session)):
    run = owned_run(session, run_id, caller)
    document = session.get(Document, run.document_id)
    doc_type = document.doc_type if document else None
    classify = session.scalar(select(Step).where(Step.run_id == run_id, Step.stage == "classify"))
    return {
        "id": run.id,
        "document_id": run.document_id,
        "page_count": document.page_count if document else 0,
        "doc_type": doc_type,
        "type_label": document.type_label if document else None,
        "classification": {"summary": classify.output_summary, "reason": classify.reason} if classify else None,
        "message": UNSUPPORTED_MESSAGE if doc_type is not None and spec_for(doc_type) is None else None,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "cost_usd": run.cost_usd,
    }


@router.get("/runs/{run_id}/steps")
def get_steps(run_id: int, session: Session = Depends(get_session), caller: str = Depends(current_session)):
    owned_run(session, run_id, caller)
    return [step_json(s) for s in session.scalars(select(Step).where(Step.run_id == run_id).order_by(Step.seq))]


@router.get("/runs/{run_id}/fields")
def get_fields(run_id: int, session: Session = Depends(get_session), caller: str = Depends(current_session)):
    owned_run(session, run_id, caller)
    return [field_json(f) for f in session.scalars(select(Field).where(Field.run_id == run_id).order_by(Field.id))]


@router.get("/runs/{run_id}/obligations")
def get_obligations(run_id: int, session: Session = Depends(get_session), caller: str = Depends(current_session)):
    owned_run(session, run_id, caller)
    rows = session.scalars(select(Obligation).where(Obligation.run_id == run_id).order_by(Obligation.id))
    return [obligation_json(o) for o in rows]


@router.get("/runs/{run_id}/brief")
def get_brief(
    run_id: int,
    today: date | None = None,
    session: Session = Depends(get_session),
    caller: str = Depends(current_session),
):
    """Decision brief, recomputed from the stored fields (deterministic, no LLM). Every line cites its source fields."""
    run = owned_run(session, run_id, caller)
    document = session.get(Document, run.document_id)
    spec = spec_for((document.doc_type if document else None) or "lease")  # runs from before classification are leases
    if spec is None:
        return {"lines": [], "skipped": []}
    rows = list(session.scalars(select(Field).where(Field.run_id == run_id).order_by(Field.id)))
    validated = [
        ValidatedField(f.name, f.value_json, f.source_quote, f.page_no, f.bbox, f.confidence or 0.0, 0.0,
                       list(f.flags or []), False, f.bboxes or [], f.label)
        for f in rows
    ]
    return brief_json(spec.brief(validated, today), {f.name: f for f in rows})


@router.get("/runs/{run_id}/actions")
def get_actions(run_id: int, session: Session = Depends(get_session), caller: str = Depends(current_session)):
    owned_run(session, run_id, caller)
    fields = {f.id: f for f in session.scalars(select(Field).where(Field.run_id == run_id))}
    actions = session.scalars(select(ProposedAction).where(ProposedAction.run_id == run_id).order_by(ProposedAction.id))
    return [action_json(session, a, fields) for a in actions]


# ---------------------------------------------------------------------------------------------------------------
# the event stream: same event names and shapes as GET /demo/{fixture}/events
# ---------------------------------------------------------------------------------------------------------------

def _sse(seq: int, name: str, data: dict) -> str:
    return f"id: {seq}\nevent: {name}\ndata: {json.dumps(data, separators=(',', ':'), default=str)}\n\n"


def _fetch_events(factory: Callable, run_id: int, after: int) -> tuple[list[tuple[int, str, dict]], bool]:
    """Events after `after`, and whether the stream is over (run finished or gone and nothing is left to send).
    Action events are refreshed with the action's current status, so a replay shows what was already approved."""
    with factory() as session:
        run = session.get(Run, run_id)
        if run is None:
            return [], True
        rows = session.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after).order_by(RunEvent.seq)
        ).all()
        out = []
        fields = actions = None
        for row in rows:
            data = row.data
            if row.name == "action":
                if actions is None:
                    fields = {f.id: f for f in session.scalars(select(Field).where(Field.run_id == run_id))}
                    actions = {a.id: a for a in session.scalars(select(ProposedAction).where(ProposedAction.run_id == run_id))}
                action = actions.get(data.get("id"))
                if action is not None:
                    data = {**data, **action_json(session, action, fields)}
            out.append((row.seq, row.name, data))
        finished = any(name == "done" for _, name, _ in out) or (not out and run.status in jobs.TERMINAL)
        return out, finished


async def stream_events(factory: Callable, run_id: int, after: int, poll: float = STREAM_POLL_SECONDS):
    last, idle = after, 0.0
    yield "retry: 1500\n\n"
    while True:
        rows, finished = await asyncio.to_thread(_fetch_events, factory, run_id, last)
        for seq, name, data in rows:
            last = seq
            yield _sse(seq, name, data)
        if finished:
            return
        if rows:
            idle = 0.0
            continue
        idle += poll
        if idle >= STREAM_HEARTBEAT_SECONDS:
            idle = 0.0
            yield ": keepalive\n\n"
        await asyncio.sleep(poll)


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: int,
    request: Request,
    after: int | None = None,
    factory: Callable = Depends(session_factory),
    caller: str = Depends(current_session),
):
    """Server-sent events for a run: everything so far from the database, then new events as the worker writes them.
    A reconnecting EventSource sends Last-Event-ID and receives only what it missed."""
    if not config.DATABASE_URL:
        raise HTTPException(503, "No database is configured on this deployment.")
    with factory() as session:  # checked and closed at once: the stream itself opens a short session per poll
        owned_run(session, run_id, caller)
    header = request.headers.get("last-event-id", "")
    start = after if after is not None else (int(header) if header.isdigit() else 0)
    return StreamingResponse(
        stream_events(factory, run_id, start),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------------------------------------------
# approving and rejecting
# ---------------------------------------------------------------------------------------------------------------

def _get_pending_action(session: Session, action_id: int, caller: str) -> ProposedAction:
    action = owned_action(session, action_id, caller)
    if action.status != "pending":
        raise HTTPException(409, f"Action is already {action.status}")
    return action


@router.post("/actions/{action_id}/approve")
def approve_action(action_id: int, session: Session = Depends(get_session), caller: str = Depends(current_session)):
    action = _get_pending_action(session, action_id, caller)
    action.status = "approved"
    execute_action(session, action)  # sandbox adapter: nothing leaves the system
    if action.group_mode == "exclusive":
        siblings = session.scalars(
            select(ProposedAction).where(
                ProposedAction.run_id == action.run_id,
                ProposedAction.group_key == action.group_key,
                ProposedAction.id != action.id,
                ProposedAction.status == "pending",
            )
        )
        for sibling in siblings:
            sibling.status = "rejected"
            sibling.policy_reason = f"Auto-rejected: action {action.id} from the same choice was approved. {sibling.policy_reason or ''}".strip()
    session.commit()
    fields = {f.id: f for f in session.scalars(select(Field).where(Field.run_id == action.run_id))}
    return action_json(session, action, fields)


@router.post("/actions/{action_id}/reject")
def reject_action(action_id: int, session: Session = Depends(get_session), caller: str = Depends(current_session)):
    action = _get_pending_action(session, action_id, caller)
    action.status = "rejected"
    session.commit()
    fields = {f.id: f for f in session.scalars(select(Field).where(Field.run_id == action.run_id))}
    return action_json(session, action, fields)


# ---------------------------------------------------------------------------------------------------------------
# stored files: only the pages and PDF of a document the caller owns
# ---------------------------------------------------------------------------------------------------------------

@router.get("/files/{key:path}", include_in_schema=False)
def get_file(
    key: str,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    caller: str = Depends(current_session),
):
    match = _FILE_KEY.match(key)
    document = session.scalar(select(Document).where(Document.sha256 == match.group(1))) if match else None
    if document is None:
        raise HTTPException(404, "File not found")
    owned_document(session, document.id, caller)
    try:
        with storage.open(key) as f:
            content = f.read()
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "File not found") from None
    media_type = "application/pdf" if key.endswith(".pdf") else "image/png"
    return Response(content, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})
