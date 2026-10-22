"""A public-upload app on in-memory SQLite: the `live` fixture, SSE parsing, and a helper to run queued jobs."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from paperbound import jobs
from paperbound.api.main import app
from paperbound.db.models import Base
from paperbound.db.session import get_session, session_factory
from paperbound.pipeline.classify import Classification
from paperbound.storage import LocalStorage, get_storage
from tests.helpers import enable_uploads, log_in, new_visitor
from tests.lease_fakes import LEASE, TODAY, fake_extractor, fake_planner


def parse_sse(text: str) -> list[tuple[int | None, str, dict]]:
    """[(id, event, data)] from an event-stream body. Comments and the retry line are skipped."""
    frames = []
    for block in text.strip().split("\n\n"):
        fields = {}
        for line in block.split("\n"):
            if line.startswith(":") or not line:
                continue
            key, _, value = line.partition(": ")
            fields[key] = value
        if "event" in fields:
            frames.append((int(fields["id"]) if "id" in fields else None, fields["event"], json.loads(fields["data"])))
    return frames


def lease_pipeline(extractor=fake_extractor, planner=fake_planner, doc_type="lease"):
    return lambda: {
        "extractor": extractor, "planner": planner, "today": TODAY,
        "classifier": lambda pages: Classification(doc_type, "heuristic", "test"),
    }


class Live:
    def __init__(self, client, Session, storage, monkeypatch, cookie, session_hash):
        self.client, self.Session, self.storage, self.monkeypatch = client, Session, storage, monkeypatch
        self.cookie, self.hash = cookie, session_hash

    def use_pipeline(self, overrides):
        self.monkeypatch.setattr(jobs, "pipeline_overrides", overrides)

    def visitor(self) -> TestClient:
        """Another browser: its own cookie jar and its own session."""
        other = TestClient(app)
        log_in(other, new_visitor()[0])
        return other

    def upload(self, data: bytes | None = None, name="lease.pdf", client=None, ip=None):
        headers = {"Fly-Client-IP": ip} if ip else {}
        data = LEASE.read_bytes() if data is None else data
        return (client or self.client).post("/documents", files={"file": (name, data, "application/pdf")}, headers=headers)

    def work(self) -> int:
        """Run every queued job to completion, as the worker would."""
        n = 0
        while jobs.process_one(self.Session):
            n += 1
        return n

    def upload_and_run(self, data=None, name="lease.pdf", client=None) -> dict:
        response = self.upload(data, name, client)
        assert response.status_code == 200, response.text
        self.work()
        return response.json()

    def first_events(self, run_id: int, count: int, client=None, **params):
        """The first `count` events of a stream that may not be finished (a queued run), then hang up."""
        text = ""
        with (client or self.client).stream("GET", f"/runs/{run_id}/events", params=params) as response:
            assert response.status_code == 200
            for chunk in response.iter_text():
                text += chunk
                if len(parse_sse(text)) >= count:
                    break
        return parse_sse(text)[:count]

    def events(self, run_id: int, client=None, **params):
        response = (client or self.client).get(f"/runs/{run_id}/events", params=params)
        assert response.status_code == 200, response.text
        return parse_sse(response.text)


@pytest.fixture()
def live(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    def override():
        with Session() as session:
            yield session

    storage = LocalStorage(tmp_path)
    app.dependency_overrides[get_session] = override
    app.dependency_overrides[session_factory] = lambda: Session
    app.dependency_overrides[get_storage] = lambda: storage
    enable_uploads(monkeypatch)
    cookie, session_hash = new_visitor()
    client = TestClient(app)
    log_in(client, cookie)
    env = Live(client, Session, storage, monkeypatch, cookie, session_hash)
    env.use_pipeline(lease_pipeline())
    yield env
    app.dependency_overrides.clear()
