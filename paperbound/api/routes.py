import hashlib
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from paperbound import config
from paperbound.db.models import Document, Field, Obligation, Page, Run, Step
from paperbound.db.session import get_session
from paperbound.pipeline.parse import ParsedPage, parse_pdf
from paperbound.pipeline.runner import make_parse_step, run_pipeline
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

    result = run_pipeline(pages, make_parse_step(pages, parse_ms, reused))

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
    return {
        "id": run.id,
        "document_id": run.document_id,
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


@router.get("/files/{key:path}", include_in_schema=False)
def get_file(key: str):
    base = config.STORAGE_DIR.resolve()
    path = (base / key).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path)
