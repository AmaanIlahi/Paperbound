import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from paperbound import config
from paperbound.db.models import Document, Page, Run
from paperbound.db.session import get_session
from paperbound.pipeline.parse import parse_pdf
from paperbound.storage import Storage, get_storage

router = APIRouter()


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

    run = Run(
        document_id=document.id,
        status="done",
        finished_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.commit()
    return {
        "document_id": document.id,
        "run_id": run.id,
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


@router.get("/files/{key:path}", include_in_schema=False)
def get_file(key: str):
    base = config.STORAGE_DIR.resolve()
    path = (base / key).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path)
