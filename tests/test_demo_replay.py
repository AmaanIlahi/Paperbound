"""Demo mode: recorded snapshots, the replay engine and its endpoints. No database, no network, no OpenAI."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paperbound.api.main import app
from paperbound.demo import replay

FIXTURES = ("lease", "insurance_certificate")
REPO = Path(__file__).resolve().parent.parent


def parse_sse(text: str) -> list[tuple[str, dict]]:
    frames = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), block
        frames.append((lines[0][7:], json.loads(lines[1][6:])))
    return frames


@pytest.fixture(params=FIXTURES)
def snap(request):
    return replay.load_snapshot(request.param)


# ---- the committed snapshots are real, complete and honest ----

def test_both_demo_fixtures_are_listed_with_their_badge():
    listed = {f["id"]: f for f in replay.list_fixtures()}
    assert set(listed) == set(FIXTURES)
    lease = listed["lease"]
    assert lease["label"] == "Lease" and listed["insurance_certificate"]["label"] == "Insurance Certificate"
    assert lease["badge"] == f"Demo replay · recorded {lease['recorded_at'][:10]} · as of 2026-10-22 · model gpt-4o-mini"
    assert date.fromisoformat(lease["recorded_at"][:10])


def test_snapshot_has_everything_section_2_asks_for(snap):
    folder = REPO / "fixtures" / "demo" / snap["fixture"]
    assert (folder / "source.pdf").read_bytes().startswith(b"%PDF")
    assert snap["status"] == "done" and snap["as_of"] == "2026-10-22" and snap["model"] == "gpt-4o-mini"
    assert [p["page_no"] for p in snap["pages"]] == list(range(1, snap["page_count"] + 1))
    for page in snap["pages"]:
        assert page["words"] and page["width_pt"] > 0
        assert (folder / "pages" / f"{page['page_no']}.png").read_bytes().startswith(b"\x89PNG")
    assert [s["stage"] for s in snap["steps"]] == ["parse", "classify", "extract", "validate", "obligations", "impact", "planner"]
    assert all(s["status"] == "done" and s["duration_ms"] >= 0 for s in snap["steps"])
    assert next(s for s in snap["steps"] if s["stage"] == "extract")["tokens"] > 0
    assert snap["fields"] and all(f["bboxes"] for f in snap["fields"] if f["value"] is not None and "ungrounded" not in f["flags"])
    assert snap["obligations"] and snap["brief"]["lines"] and snap["actions"]
    assert all(a["status"] == "pending" and a["sources"] for a in snap["actions"])


def test_snapshot_actions_carry_their_groups(snap):
    groups = {(a["tool"], a["group_key"], a["group_mode"]) for a in snap["actions"]}
    assert ("create_calendar_event", "reminders", "bundle") in groups
    if snap["fixture"] == "lease":
        assert ("draft_email", "notice_choice", "exclusive") in groups
    assert any(a["tool"] == "upsert_record" for a in snap["actions"])
    assert not any("{{RUN_ID}}" in json.dumps(a["args"]) for a in snap["actions"])  # links point at the demo, not a run
    assert any(f"/#demo={snap['fixture']}&page=" in json.dumps(a["args"]) for a in snap["actions"])


def test_the_recorded_brief_is_what_the_code_computes_from_the_recorded_fields(snap):
    """Guards against a hand-edited snapshot: the brief must follow from the fields it cites."""
    from paperbound.doctypes import SUPPORTED
    from paperbound.pipeline.types import ValidatedField

    fields = [ValidatedField(f["name"], f["value"], f["source_quote"], f["page_no"], f["bbox"], f["confidence"], 0.0,
                             f["flags"], False, f["bboxes"]) for f in snap["fields"]]
    brief = SUPPORTED[snap["doc_type"]].brief(fields, date.fromisoformat(snap["as_of"]))
    recorded = {ln["key"]: ln["value"] for ln in snap["brief"]["lines"]}
    assert {ln.key: ln.value for ln in brief.lines} == recorded
    by_id = {f["id"]: f for f in snap["fields"]}
    assert all(by_id[s["field_id"]]["name"] == s["name"] for ln in snap["brief"]["lines"] for s in ln["sources"])


def test_narration_covers_every_step_and_the_gate(snap):
    steps = snap["narration"]["steps"]
    assert set(steps) >= {s["stage"] for s in snap["steps"]} | {"gate"}
    assert all(len(text) > 60 for text in steps.values()) and snap["narration"]["intro"] and snap["narration"]["outro"]
    # each model step says where the LLM was used and each code step says it is code
    assert "model" in steps["extract"].lower() and "no model" in steps["parse"].lower() and "no model" in steps["classify"].lower()
    assert "code" in steps["validate"].lower() and "no model" in steps["obligations"].lower()


# ---- timing ----

def test_step_replay_time_is_the_real_duration_capped_with_a_floor():
    assert replay.step_seconds(0) == replay.MIN_STEP_SECONDS
    assert replay.step_seconds(900) == 0.9
    assert replay.step_seconds(9194) == replay.STEP_CAP_SECONDS == 1.5
    assert replay.step_seconds(10**9) == 1.5


def test_no_wait_in_the_replay_exceeds_the_cap(snap):
    events = replay.build_events(snap)
    assert max(delay for delay, _, _ in events) <= replay.STEP_CAP_SECONDS
    extract = [i for i, (_, name, d) in enumerate(events) if name == "step" and d["stage"] == "extract"]
    running, done = extract
    # the whole extract step, including its progressive field reveal, takes the capped 1.5 s
    assert sum(delay for delay, _, _ in events[running + 1: done + 1]) == pytest.approx(1.5)


def test_streaming_sleeps_exactly_the_planned_delays(monkeypatch, snap):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(replay.asyncio, "sleep", fake_sleep)

    async def run(speed):
        return [frame async for frame in replay.stream(snap, speed)]

    planned = [d for d, _, _ in replay.build_events(snap) if d > 0]
    asyncio.run(run(1.0))
    assert slept == pytest.approx(planned)
    slept.clear()
    asyncio.run(run(0))
    assert slept == []  # speed 0 sends everything at once


# ---- event sequence ----

def test_event_sequence_reveals_fields_progressively(snap):
    events = replay.build_events(snap)
    names = [n for _, n, _ in events]
    assert names[0] == "meta" and names[-1] == "done"
    extracted = [d for _, n, d in events if n == "field" and d["phase"] == "extracted"]
    validated = [d for _, n, d in events if n == "field" and d["phase"] == "validated"]
    assert [f["name"] for f in extracted] == [f["name"] for f in snap["fields"]] == [f["name"] for f in validated]
    assert all("confidence" not in f for f in extracted) and all("bboxes" in f for f in validated)
    stages = [d["stage"] for _, n, d in events if n == "step" and d["status"] != "running"]
    assert stages == [s["stage"] for s in snap["steps"]] + ["gate"]
    index = {n: i for i, n in enumerate(names)}
    assert index["meta"] < index["field"] < index["obligations"] < index["brief"] < index["action"] < index["done"]
    assert names.count("action") == len(snap["actions"])
    done_steps = [d for _, n, d in events if n == "step" and d["status"] != "running"]
    assert all(d["narration"] for d in done_steps)
    # a step is announced as running before it finishes
    seen = set()
    for _, n, d in events:
        if n == "step":
            seen.add((d["stage"], d["status"]))
            if d["status"] != "running" and d["stage"] != "gate":
                assert (d["stage"], "running") in seen


def test_meta_carries_the_badge_and_done_carries_the_totals(snap):
    events = replay.build_events(snap)
    meta, done = events[0][2], events[-1][2]
    assert meta["badge"].startswith("Demo replay · recorded ") and meta["as_of"] == "2026-10-22" and meta["model"] == "gpt-4o-mini"
    assert done["status"] == "done" and done["tokens"] == snap["tokens"] and done["cost_usd"] == snap["cost_usd"]


# ---- HTTP ----

@pytest.fixture()
def client():
    return TestClient(app)


def test_fixtures_endpoint(client):
    assert {f["id"] for f in client.get("/demo/fixtures").json()} == set(FIXTURES)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_events_endpoint_streams_the_whole_run(client, fixture):
    response = client.get(f"/demo/{fixture}/events", params={"speed": 0})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    frames = parse_sse(response.text)
    assert frames[0][0] == "meta" and frames[-1][0] == "done"
    assert [n for n, _ in frames] == [n for _, n, _ in replay.build_events(replay.load_snapshot(fixture))]


def test_page_endpoint_has_words_and_an_image_url(client):
    page = client.get("/demo/lease/pages/2").json()
    assert page["page_no"] == 2 and page["words"] and page["width_pt"] > 0
    assert page["image_url"] == "/demo/lease/pages/2/image.png"
    image = client.get(page["image_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png" and image.content.startswith(b"\x89PNG")


def test_unknown_things_are_404(client):
    for url in ("/demo/nope/events", "/demo/nope/pages/1", "/demo/lease/pages/99", "/demo/lease/pages/99/image.png",
                "/demo/Lease/events", "/demo/..%2f..%2fREADME/events", "/demo/lease%2F..%2Flease/events"):
        assert client.get(url).status_code == 404, url


def test_fixture_names_cannot_leave_the_demo_directory():
    for bad in ("..", "../lease", "lease/../lease", "", "a" * 65, "Lease", "lease.json"):
        with pytest.raises(replay.FixtureNotFound):
            replay.fixture_path(bad)


# ---- demo mode touches neither the database nor OpenAI ----

def test_the_replay_modules_import_no_database_and_no_openai():
    code = (
        "import sys\n"
        "import paperbound.demo.replay, paperbound.demo.routes\n"
        "bad = [m for m in ('openai', 'sqlalchemy', 'psycopg', 'alembic', 'paperbound.db', 'paperbound.api', 'paperbound.pipeline',"
        " 'paperbound.agent') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP")}
    env["PAPERBOUND_SKIP_DOTENV"] = "1"
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO, env={**env, "PYTHONPATH": str(REPO)},
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_demo_endpoints_work_when_the_database_and_openai_would_explode(monkeypatch, client):
    import openai

    from paperbound.db.session import get_session

    def boom(*args, **kwargs):
        raise AssertionError("demo mode must not use the database or OpenAI")

    monkeypatch.setattr(openai, "OpenAI", boom)
    app.dependency_overrides[get_session] = boom
    try:
        assert client.get("/demo/fixtures").status_code == 200
        assert client.get("/demo/lease/events", params={"speed": 0}).status_code == 200
        assert client.get("/demo/insurance_certificate/pages/1").status_code == 200
        assert client.get("/demo/insurance_certificate/pages/1/image.png").status_code == 200
    finally:
        app.dependency_overrides.pop(get_session, None)


# ---- the app starts with no secrets at all ----

def test_app_starts_and_serves_the_demo_with_no_secrets_and_no_database():
    code = (
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "from paperbound import config\n"
        "assert config.DATABASE_URL == '' and config.OPENAI_API_KEY == ''\n"
        "from paperbound.api.main import app\n"
        "from paperbound.db import session\n"
        "with TestClient(app) as c:\n"
        "    assert c.get('/').status_code == 200 and c.get('/healthz').json() == {'ok': True}\n"
        "    cfg = c.get('/config').json()\n"
        "    assert cfg['live_uploads_enabled'] is False, cfg\n"
        "    assert len(c.get('/demo/fixtures').json()) == 2\n"
        "    assert c.get('/demo/lease/events', params={'speed': 0}).status_code == 200\n"
        "    up = c.post('/documents', files={'file': ('a.pdf', b'%PDF-1.4', 'application/pdf')})\n"
        "    assert up.status_code == 403, up.text\n"
        "    assert c.get('/runs/1').status_code == 503\n"
        "assert session._engine is None  # no engine was ever created\n"
        "print('ok')\n"
    )
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP")}
    env.update({"PAPERBOUND_SKIP_DOTENV": "1", "PYTHONPATH": str(REPO)})
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stdout + done.stderr
