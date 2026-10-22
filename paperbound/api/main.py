from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from paperbound import config
from paperbound.api import routes

app = FastAPI(title="Paperbound")
app.include_router(routes.router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.WEB_DIR / "index.html")


app.mount("/web", StaticFiles(directory=config.WEB_DIR), name="web")
