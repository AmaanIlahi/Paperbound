import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from paperbound import config, deploy
from paperbound.api import routes
from paperbound.demo import routes as demo_routes

log = logging.getLogger(__name__)
CLEANUP_INTERVAL_SECONDS = 15 * 60


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
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Delete expired uploads periodically, but only when live uploads and a database are both configured."""
    task = asyncio.create_task(_cleanup_loop()) if deploy.live_enabled() and config.DATABASE_URL else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Paperbound", lifespan=lifespan)
app.include_router(demo_routes.router)
app.include_router(routes.router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.WEB_DIR / "index.html")


app.mount("/web", StaticFiles(directory=config.WEB_DIR), name="web")
