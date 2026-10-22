import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from typing import Callable

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from paperbound import config, deploy, evals_page, jobs, sessions
from paperbound.api import routes
from paperbound.db.session import session_factory
from paperbound.demo import routes as demo_routes

log = logging.getLogger(__name__)


def _cleanup_once() -> None:
    from paperbound import usage
    from paperbound.db.session import new_session
    from paperbound.storage import get_storage

    with new_session() as session:
        usage.cleanup_expired(session, get_storage(), datetime.now(timezone.utc))


async def _cleanup_loop():
    while True:
        try:
            await asyncio.to_thread(_cleanup_once)
        except Exception:
            log.exception("expired-upload cleanup failed")
        await asyncio.sleep(deploy.cleanup_interval_seconds())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Uploads are on only with a database and an OpenAI key. Then one worker runs queued jobs in this process and a
    second task deletes expired uploads; without them the app still serves the samples."""
    tasks = [asyncio.create_task(_cleanup_loop()), asyncio.create_task(jobs.worker_loop())] if deploy.uploads_enabled() else []
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Paperbound", lifespan=lifespan)
app.include_router(demo_routes.router)
app.include_router(routes.router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/healthz/db", include_in_schema=False)
def healthz_db(factory: Callable = Depends(session_factory)):
    """Runs SELECT 1 against the database: 200 if it answers, 503 if not. Used by the daily keep-alive, which also stops
    a free-tier project from pausing for inactivity. `/healthz` stays database-free."""
    try:
        with factory() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        log.exception("database health check failed")
        return JSONResponse({"ok": False, "database": "unreachable"}, status_code=503)
    return {"ok": True, "database": "up"}


@app.get("/", include_in_schema=False)
def index(request: Request):
    response = FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})
    if sessions.peek_session(request) is None:  # the visitor's session starts on their first page load
        sessions.set_cookie(response, request, sessions.new_cookie()[0])
    return response


@app.get("/evals", include_in_schema=False)
def evals():
    return HTMLResponse(evals_page.render())


app.mount("/web", StaticFiles(directory=config.WEB_DIR), name="web")
