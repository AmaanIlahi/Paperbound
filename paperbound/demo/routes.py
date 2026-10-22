"""Demo endpoints. Imports only FastAPI and the replay engine: no database, no OpenAI."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from paperbound.demo import replay

router = APIRouter(prefix="/demo")


def _snapshot(fixture: str) -> dict:
    try:
        return replay.load_snapshot(fixture)
    except replay.FixtureNotFound:
        raise HTTPException(404, "Unknown demo fixture") from None


@router.get("/fixtures")
def fixtures():
    return replay.list_fixtures()


@router.get("/{fixture}/events")
def events(fixture: str, speed: float = 1.0):
    snapshot = _snapshot(fixture)
    return StreamingResponse(
        replay.stream(snapshot, max(0.0, min(speed, 10.0))),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{fixture}/pages/{page_no}")
def page(fixture: str, page_no: int):
    payload = replay.page_payload(_snapshot(fixture), fixture, page_no)
    if payload is None:
        raise HTTPException(404, "Page not found")
    return payload


@router.get("/{fixture}/pages/{page_no}/image.png")
def page_image(fixture: str, page_no: int):
    _snapshot(fixture)
    path = replay.image_path(fixture, page_no)
    if path is None:
        raise HTTPException(404, "Page image not found")
    return FileResponse(path, media_type="image/png")
