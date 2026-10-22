import hashlib
import time
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from paperbound import config
from paperbound.agent.executor import execute_action
from paperbound.agent.planner import resolve_placeholders
from paperbound.agent.tools import get_tool
from paperbound.db.models import (
    ActionResult,
    Document,
    Field,
    Obligation,
    Page,
    ProposedAction,
    Run,
    Step,
)
from paperbound.db.session import get_session
from paperbound.doctypes import SUPPORTED, UNSUPPORTED_MESSAGE
from paperbound.pipeline.parse import ParsedPage, parse_pdf
from paperbound.pipeline.runner import make_parse_step, run_pipeline
from paperbound.pipeline.types import ValidatedField
from paperbound.storage import Storage, get_storage

router = APIRouter()


def _pages_from_db(document: Document) -> list[ParsedPage]:
    return [
        ParsedPage(
            page_no=p.page_no,
            route=p.route,
            text=p.text,
            words=p.words_json,
            quality_score=p.quality_score,
            width_pt=p.width_pt,
            height_pt=p.height_pt,
            image_png=b"",
        )
        for p in document.pages
    ]


@router.post("/documents")
def upload_document(
    file: UploadFile,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
):
    data = file.file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Not a PDF file")

    sha256 = hashlib.sha256(data).hexdigest()
    document = session.scalar(select(Document).where(Document.sha256 == sha256))
    reused = document is not None

    started = time.perf_counter()
    if document is None:
        try:
            pages = parse_pdf(data)
        except Exception as exc:
            raise HTTPException(400, f"Could not parse PDF: {exc}") from exc

        storage_key = f"documents/{sha256}/source.pdf"
        storage.save(storage_key, data)
        document = Document(
            filename=file.filename or "document.pdf",
            storage_key=storage_key,
            sha256=sha256,
            page_count=len(pages),
        )
        for p in pages:
            image_key = f"documents/{sha256}/pages/{p.page_no}.png"
            storage.save(image_key, p.image_png)
            document.pages.append(
                Page(
                    page_no=p.page_no,
                    route=p.route,
                    text=p.text,
                    words_json=p.words,
                    quality_score=p.quality_score,
                    image_key=image_key,
                    width_pt=p.width_pt,
                    height_pt=p.height_pt,
                )
            )
        session.add(document)
        session.flush()
    else:
        pages = _pages_from_db(document)
    parse_ms = int((time.perf_counter() - started) * 1000)

    run = Run(document_id=document.id, status="running")
    session.add(run)
    session.flush()

    earlier = session.scalars(
        select(Obligation)
        .join(Run, Obligation.run_id == Run.id)
        .where(Run.document_id == document.id, Run.id != run.id)
    )
    existing = [
        {"kind": o.kind, "due_date": o.due_date.isoformat() if o.due_date else None, "description": o.description}
        for o in earlier
    ]
    result = run_pipeline(
        pages, make_parse_step(pages, parse_ms, reused), existing_obligations=existing
    )

    if result.doc_type:
        document.doc_type = result.doc_type
    for seq, step in enumerate(result.steps, start=1):
        session.add(Step(run_id=run.id, seq=seq, **vars(step)))
    field_ids = {}
    for f in result.fields:
        row = Field(
            run_id=run.id,
            name=f.name,
            value_json=f.value,
            source_quote=f.source_quote,
            page_no=f.page_no,
            bbox=f.bbox,
            bboxes=f.bboxes,
            confidence=f.confidence,
            flags=f.flags,
        )
        session.add(row)
        session.flush()
        field_ids[f.name] = row.id
    for o in result.obligations:
        session.add(
            Obligation(
                run_id=run.id,
                kind=o.kind,
                due_date=o.due_date,
                description=o.description,
                source_field_ids=[field_ids[name] for name in o.source_fields if name in field_ids],
            )
        )
    for planned in result.plan.actions if result.plan else []:
        action = ProposedAction(
            run_id=run.id,
            tool=planned.tool,
            args_json=resolve_placeholders(planned.args, run.id),
            source_field_ids=[field_ids[name] for name in planned.source_fields if name in field_ids],
            rationale=planned.rationale,
            status="pending",
            policy_reason=planned.policy_reason,
            group_key=planned.group_key,
            group_mode=planned.group_mode,
        )
        session.add(action)
        session.flush()
        if planned.policy_status == "approved":  # auto-approval is off by default
            action.status = "approved"
            execute_action(session, action)
    run.status = result.status
    run.cost_usd = result.cost_usd
    run.finished_at = datetime.now(timezone.utc)
    session.commit()
    return {
        "document_id": document.id,
        "run_id": run.id,
        "status": run.status,
        "page_count": document.page_count,
        "reused": reused,
        "doc_type": result.doc_type,
        "message": result.message,
    }


@router.get("/documents/{document_id}/pages/{page_no}")
def get_page(
    document_id: int,
    page_no: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
):
    page = session.scalar(
        select(Page).where(Page.document_id == document_id, Page.page_no == page_no)
    )
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


def _get_run(session: Session, run_id: int) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session)):
    run = _get_run(session, run_id)
    document = session.get(Document, run.document_id)
    doc_type = document.doc_type if document else None
    classify = session.scalar(select(Step).where(Step.run_id == run_id, Step.stage == "classify"))
    return {
        "id": run.id,
        "document_id": run.document_id,
        "page_count": document.page_count if document else 0,
        "doc_type": doc_type,
        "classification": {"summary": classify.output_summary, "reason": classify.reason} if classify else None,
        "message": UNSUPPORTED_MESSAGE if doc_type is not None and doc_type not in SUPPORTED else None,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "cost_usd": run.cost_usd,
    }


@router.get("/runs/{run_id}/steps")
def get_steps(run_id: int, session: Session = Depends(get_session)):
    _get_run(session, run_id)
    steps = session.scalars(select(Step).where(Step.run_id == run_id).order_by(Step.seq))
    return [
        {
            "seq": s.seq,
            "stage": s.stage,
            "status": s.status,
            "summary": s.output_summary,
            "input_summary": s.input_summary,
            "reason": s.reason,
            "error": s.error,
            "model": s.model,
            "tokens": s.tokens,
            "cost_usd": s.cost_usd,
            "duration_ms": s.duration_ms,
            "ts_offset_ms": s.ts_offset_ms,
        }
        for s in steps
    ]


@router.get("/runs/{run_id}/fields")
def get_fields(run_id: int, session: Session = Depends(get_session)):
    _get_run(session, run_id)
    fields = session.scalars(select(Field).where(Field.run_id == run_id).order_by(Field.id))
    return [
        {
            "id": f.id,
            "name": f.name,
            "value": f.value_json,
            "source_quote": f.source_quote,
            "page_no": f.page_no,
            "bbox": f.bbox,
            "bboxes": f.bboxes or ([f.bbox] if f.bbox else []),
            "confidence": f.confidence,
            "flags": f.flags,
        }
        for f in fields
    ]


@router.get("/runs/{run_id}/obligations")
def get_obligations(run_id: int, session: Session = Depends(get_session)):
    _get_run(session, run_id)
    rows = session.scalars(select(Obligation).where(Obligation.run_id == run_id).order_by(Obligation.id))
    return [
        {
            "id": o.id,
            "kind": o.kind,
            "due_date": o.due_date,
            "description": o.description,
            "source_field_ids": o.source_field_ids,
        }
        for o in rows
    ]


@router.get("/runs/{run_id}/brief")
def get_brief(run_id: int, today: date | None = None, session: Session = Depends(get_session)):
    """Decision brief, recomputed from the stored fields (deterministic, no LLM). Every line cites its source fields."""
    run = _get_run(session, run_id)
    document = session.get(Document, run.document_id)
    spec = SUPPORTED.get((document.doc_type if document else None) or "lease")  # runs from before classification are leases
    if spec is None:
        return {"lines": [], "skipped": []}
    rows = list(session.scalars(select(Field).where(Field.run_id == run_id).order_by(Field.id)))
    validated = [
        ValidatedField(f.name, f.value_json, f.source_quote, f.page_no, f.bbox, f.confidence or 0.0, 0.0,
                       list(f.flags or []), False, f.bboxes or [])
        for f in rows
    ]
    by_name = {f.name: f for f in rows}
    brief = spec.brief(validated, today)
    return {
        "lines": [
            {
                "key": ln.key,
                "label": ln.label,
                "value": ln.value,
                "text": ln.text,
                "note": ln.note,
                "sources": [
                    {
                        "field_id": by_name[n].id,
                        "name": n,
                        "page_no": by_name[n].page_no,
                        "bboxes": by_name[n].bboxes or ([by_name[n].bbox] if by_name[n].bbox else []),
                    }
                    for n in ln.source_fields
                    if n in by_name
                ],
            }
            for ln in brief.lines
        ],
        "skipped": brief.skipped,
    }


@router.get("/runs/{run_id}/actions")
def get_actions(run_id: int, session: Session = Depends(get_session)):
    _get_run(session, run_id)
    fields = {f.id: f for f in session.scalars(select(Field).where(Field.run_id == run_id))}
    actions = session.scalars(select(ProposedAction).where(ProposedAction.run_id == run_id).order_by(ProposedAction.id))
    return [_action_json(session, a, fields) for a in actions]


def _action_json(session: Session, action: ProposedAction, fields: dict) -> dict:
    tool = get_tool(action.tool)
    result = session.scalar(
        select(ActionResult).where(ActionResult.action_id == action.id).order_by(ActionResult.id.desc())
    )
    return {
        "id": action.id,
        "run_id": action.run_id,
        "tool": action.tool,
        "side_effect": bool(tool and tool.side_effect),
        "args": action.args_json,
        "status": action.status,
        "rationale": action.rationale,
        "policy_reason": action.policy_reason,
        "group_key": action.group_key,
        "group_mode": action.group_mode,
        "sources": [
            {
                "field_id": fid,
                "name": fields[fid].name,
                "page_no": fields[fid].page_no,
                "bboxes": fields[fid].bboxes or ([fields[fid].bbox] if fields[fid].bbox else []),
            }
            for fid in action.source_field_ids or []
            if fid in fields
        ],
        "result": (
            {"adapter": result.adapter, "response": result.response_json, "executed_at": result.executed_at}
            if result
            else None
        ),
    }


def _get_pending_action(session: Session, action_id: int) -> ProposedAction:
    action = session.get(ProposedAction, action_id)
    if action is None:
        raise HTTPException(404, "Action not found")
    if action.status != "pending":
        raise HTTPException(409, f"Action is already {action.status}")
    return action


@router.post("/actions/{action_id}/approve")
def approve_action(action_id: int, session: Session = Depends(get_session)):
    action = _get_pending_action(session, action_id)
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
    return _action_json(session, action, fields)


@router.post("/actions/{action_id}/reject")
def reject_action(action_id: int, session: Session = Depends(get_session)):
    action = _get_pending_action(session, action_id)
    action.status = "rejected"
    session.commit()
    fields = {f.id: f for f in session.scalars(select(Field).where(Field.run_id == action.run_id))}
    return _action_json(session, action, fields)


@router.get("/files/{key:path}", include_in_schema=False)
def get_file(key: str):
    base = config.STORAGE_DIR.resolve()
    path = (base / key).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path)
