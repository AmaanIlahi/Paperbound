"""Deploy modes: live uploads off by default; when on, access code, size and page limits, per-IP rate limit, daily LLM
budget in the database, and expiry of uploads."""

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from paperbound import deploy, usage
from paperbound.api import routes
from paperbound.api.main import app
from paperbound.db.models import Base, Document, Field, Page, Run, UsageLedger
from paperbound.db.session import get_session
from paperbound.pipeline.classify import Classification
from paperbound.pipeline.extract import ExtractionResult
from paperbound.pipeline.runner import run_pipeline
from paperbound.storage import LocalStorage, get_storage
from tests.test_upload_flow import LEASE, fake_extractor, fake_planner

CODE = "open-sesame"


def costly_extractor(cost):
    def extractor(pages):
        base = fake_extractor(pages)
        return ExtractionResult(base.fields, base.model, base.tokens, cost)

    return extractor


def pdf_with_pages(n: int) -> bytes:
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    for i in range(n):
        pdf.drawString(72, 700, f"page {i + 1}")
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    def override():
        with Session() as session:
            yield session

    storage = LocalStorage(tmp_path)
    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_storage] = lambda: storage
    monkeypatch.setenv("LIVE_UPLOADS_ENABLED", "true")
    monkeypatch.setenv("ACCESS_CODE", CODE)
    use_pipeline(monkeypatch, costly_extractor(0.0))
    yield TestClient(app), Session, storage, monkeypatch
    app.dependency_overrides.clear()


def use_pipeline(monkeypatch, extractor):
    monkeypatch.setattr(
        routes, "run_pipeline",
        lambda pages, step, existing_obligations=None: run_pipeline(
            pages, step, extractor=extractor, planner=fake_planner, existing_obligations=existing_obligations,
            today=datetime(2026, 10, 22).date(), classifier=lambda p: Classification("lease", "heuristic", "test"),
        ),
    )


def post(client, data=None, code=CODE, ip=None, name="lease.pdf"):
    headers = {}
    if code is not None:
        headers["X-Access-Code"] = code
    if ip:
        headers["Fly-Client-IP"] = ip
    data = LEASE.read_bytes() if data is None else data
    return client.post("/documents", files={"file": (name, data, "application/pdf")}, headers=headers)


# ---- off by default ----

def test_uploads_are_forbidden_unless_live_uploads_are_enabled(env):
    client, _, _, monkeypatch = env
    for value in (None, "", "false", "yes", "1"):
        if value is None:
            monkeypatch.delenv("LIVE_UPLOADS_ENABLED")
        else:
            monkeypatch.setenv("LIVE_UPLOADS_ENABLED", value)
        response = post(client)
        assert response.status_code == 403, value
        assert "disabled" in response.json()["detail"]
    monkeypatch.setenv("LIVE_UPLOADS_ENABLED", "TRUE")  # case does not matter
    assert post(client).status_code == 200


def test_a_disabled_deployment_answers_403_without_a_database(monkeypatch):
    def boom():
        raise AssertionError("the database must not be touched")

    monkeypatch.delenv("LIVE_UPLOADS_ENABLED", raising=False)
    app.dependency_overrides[get_session] = boom
    try:
        assert post(TestClient(app)).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_a_missing_database_is_a_503_not_a_crash(monkeypatch):
    from paperbound import config

    monkeypatch.setattr(config, "DATABASE_URL", "")
    assert TestClient(app).get("/runs/1").status_code == 503


def test_config_endpoint_describes_the_mode_and_never_leaks_the_code(env):
    client, _, _, monkeypatch = env
    body = client.get("/config").json()
    assert body["live_uploads_enabled"] is True and body["access_code_required"] is True
    assert body["max_upload_mb"] == 10 and body["max_pages"] == 20 and body["uploads_per_ip_per_hour"] == 5
    assert CODE not in client.get("/config").text
    monkeypatch.setenv("LIVE_UPLOADS_ENABLED", "false")
    assert client.get("/config").json()["live_uploads_enabled"] is False


# ---- access code ----

def test_access_code_is_required(env):
    client, _, _, _ = env
    assert post(client, code=None).status_code == 401
    assert post(client, code="").status_code == 401
    assert post(client, code="wrong").status_code == 401
    assert post(client, code=CODE + " ").status_code == 401
    assert post(client, code=CODE).status_code == 200


def test_live_mode_without_a_configured_code_refuses_everything(env):
    client, _, _, monkeypatch = env
    monkeypatch.delenv("ACCESS_CODE")
    for code in (None, "", CODE):
        assert post(client, code=code).status_code == 503


def test_access_code_comparison():
    assert deploy.check_access_code(None) is False


def test_a_rejected_code_does_not_use_up_the_rate_limit(env):
    client, Session, _, _ = env
    assert post(client, code="wrong").status_code == 401
    with Session() as s:
        assert s.scalars(select(UsageLedger)).all() == []


# ---- size and page limits ----

def test_file_over_10_mb_is_rejected(env):
    client, Session, _, _ = env
    too_big = b"%PDF-1.4\n" + b"0" * (10 * 1024 * 1024)
    response = post(client, too_big)
    assert response.status_code == 413 and "10 MB" in response.json()["detail"]
    with Session() as s:
        assert s.scalars(select(Document)).all() == []


def test_file_up_to_the_limit_is_read_and_checked_as_a_pdf(env):
    client, _, _, _ = env
    assert post(client, b"%PDF-1.4\n" + b"0" * 1000).status_code == 400  # a PDF header but not parseable
    assert post(client, b"not a pdf").status_code == 400


def test_more_than_20_pages_is_rejected_and_nothing_is_stored(env):
    client, Session, storage, _ = env
    assert post(client, pdf_with_pages(21), ip="1.1.1.1").status_code == 413
    with Session() as s:
        assert s.scalars(select(Document)).all() == []
    assert not (storage.base_dir / "documents").exists()
    assert post(client, pdf_with_pages(20), ip="1.1.1.1").status_code == 200


def test_limits_can_be_changed_with_environment_variables(env):
    client, _, _, monkeypatch = env
    monkeypatch.setenv("MAX_PAGES", "2")
    assert post(client, pdf_with_pages(3)).status_code == 413
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.001")
    assert post(client, pdf_with_pages(1)).status_code == 413


# ---- rate limit: 5 uploads per IP per hour ----

def test_sixth_upload_from_one_address_within_an_hour_is_refused(env):
    client, Session, _, _ = env
    for i in range(5):
        assert post(client, ip="9.9.9.9").status_code == 200, i
    refused = post(client, ip="9.9.9.9")
    assert refused.status_code == 429 and "5 per hour" in refused.json()["detail"]
    assert post(client, ip="8.8.8.8").status_code == 200  # another address is unaffected
    with Session() as s:
        rows = s.scalars(select(UsageLedger).where(UsageLedger.kind == "upload")).all()
        assert len(rows) == 6 and all(r.ip_hash and "9.9.9.9" not in r.ip_hash for r in rows)  # hashes only


def test_uploads_older_than_an_hour_do_not_count(env):
    client, Session, _, _ = env
    old = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)
    with Session() as s:
        for _ in range(5):
            s.add(UsageLedger(kind="upload", ip_hash=usage.ip_hash("7.7.7.7"), created_at=old))
        s.commit()
    assert post(client, ip="7.7.7.7").status_code == 200
    with Session() as s:
        s.add_all(UsageLedger(kind="upload", ip_hash=usage.ip_hash("6.6.6.6"), created_at=datetime.now(timezone.utc) - timedelta(minutes=59)) for _ in range(5))
        s.commit()
    assert post(client, ip="6.6.6.6").status_code == 429


def test_rejected_files_still_count_against_the_limit(env):
    client, _, _, _ = env
    for _ in range(5):
        assert post(client, b"not a pdf", ip="5.5.5.5").status_code == 400
    assert post(client, ip="5.5.5.5").status_code == 429


def test_the_limit_is_configurable(env):
    client, _, _, monkeypatch = env
    monkeypatch.setenv("UPLOADS_PER_IP_PER_HOUR", "1")
    assert post(client, ip="4.4.4.4").status_code == 200 and post(client, ip="4.4.4.4").status_code == 429


def test_the_address_comes_from_fly_client_ip_else_the_socket(env):
    client, Session, _, _ = env
    post(client, ip="3.3.3.3")
    post(client)  # no proxy header: the test client's own address
    with Session() as s:
        hashes = {r.ip_hash for r in s.scalars(select(UsageLedger).where(UsageLedger.kind == "upload"))}
    assert hashes == {usage.ip_hash("3.3.3.3"), usage.ip_hash("testclient")}


# ---- daily LLM budget, tracked in the database ----

def test_budget_blocks_uploads_once_spent_and_resets_the_next_day(env):
    client, Session, _, monkeypatch = env
    monkeypatch.setenv("DAILY_LLM_BUDGET_USD", "0.50")
    use_pipeline(monkeypatch, costly_extractor(0.40))
    assert post(client, ip="1.0.0.1").status_code == 200  # spent 0.40, under 0.50
    assert post(client, ip="1.0.0.2").status_code == 200  # the check happens before the run, so this one still starts
    blocked = post(client, ip="1.0.0.3")
    assert blocked.status_code == 429 and "daily LLM budget ($0.50)" in blocked.json()["detail"]
    with Session() as s:
        spent = usage.spent_today(s, datetime.now(timezone.utc))
        assert spent == pytest.approx(0.80, abs=0.01)
        # the same spend, recorded yesterday, no longer counts
        for row in s.scalars(select(UsageLedger).where(UsageLedger.kind == "llm")):
            row.created_at = datetime.now(timezone.utc) - timedelta(days=1, minutes=1)
        s.commit()
        assert usage.spent_today(s, datetime.now(timezone.utc)) == 0.0
    assert post(client, ip="1.0.0.4").status_code == 200


def test_default_budget_is_one_dollar(env):
    assert deploy.daily_llm_budget_usd() == 1.00


def test_spend_is_recorded_per_run_in_the_database(env):
    client, Session, _, monkeypatch = env
    use_pipeline(monkeypatch, costly_extractor(0.25))
    run_id = post(client).json()["run_id"]
    with Session() as s:
        run_cost = s.get(Run, run_id).cost_usd
        ledger = s.scalars(select(UsageLedger).where(UsageLedger.kind == "llm")).all()
    assert len(ledger) == 1 and ledger[0].usd == pytest.approx(run_cost) and run_cost >= 0.25


def test_a_blocked_upload_does_no_work(env):
    client, Session, _, monkeypatch = env
    monkeypatch.setenv("DAILY_LLM_BUDGET_USD", "0.01")
    with Session() as s:
        s.add(UsageLedger(kind="llm", usd=0.5, created_at=datetime.now(timezone.utc)))
        s.commit()
    assert post(client).status_code == 429
    with Session() as s:
        assert s.scalars(select(Document)).all() == []


# ---- uploads are deleted after 24 hours ----

def test_new_uploads_expire_after_24_hours(env):
    client, Session, _, _ = env
    before = datetime.now(timezone.utc)
    assert post(client).status_code == 200
    with Session() as s:
        expires = s.scalar(select(Document)).expires_at
    expires = expires.replace(tzinfo=timezone.utc) if expires.tzinfo is None else expires
    assert timedelta(hours=23, minutes=59) < expires - before < timedelta(hours=24, minutes=1)


def seed_document(Session, storage, sha, expires_at):
    key = f"documents/{sha}/source.pdf"
    storage.save(key, b"%PDF-1.4 test")
    storage.save(f"documents/{sha}/pages/1.png", b"png")
    with Session() as s:
        doc = Document(filename=f"{sha[:4]}.pdf", storage_key=key, sha256=sha, page_count=1, expires_at=expires_at)
        s.add(doc)
        s.flush()
        s.add(Page(document_id=doc.id, page_no=1, route="text_layer", text="x", words_json=[], quality_score=1.0, width_pt=1, height_pt=1))
        run = Run(document_id=doc.id, status="done")
        s.add(run)
        s.flush()
        s.add(Field(run_id=run.id, name="x", value_json=1, flags=[]))
        s.commit()
        return doc.id


def test_cleanup_deletes_only_expired_documents_with_their_files_and_children(env):
    _, Session, storage, _ = env
    now = datetime.now(timezone.utc)
    expired = seed_document(Session, storage, "a" * 64, now - timedelta(minutes=1))
    fresh = seed_document(Session, storage, "b" * 64, now + timedelta(hours=1))
    permanent = seed_document(Session, storage, "c" * 64, None)  # saved outside live-upload mode: never touched
    with Session() as s:
        s.add(UsageLedger(kind="llm", usd=0.3, created_at=now))
        s.commit()
        assert usage.cleanup_expired(s, storage, now) == 1
        assert {d for d in s.scalars(select(Document.id))} == {fresh, permanent}
        assert s.scalars(select(Run).where(Run.document_id == expired)).all() == []
        assert len(s.scalars(select(Field)).all()) == 2 and len(s.scalars(select(Page)).all()) == 2  # children went too
        assert usage.spent_today(s, now) == pytest.approx(0.3)  # the budget survives the deletion
    assert not (storage.base_dir / "documents" / ("a" * 64)).exists()
    assert (storage.base_dir / "documents" / ("b" * 64) / "source.pdf").exists()
    assert (storage.base_dir / "documents" / ("c" * 64) / "source.pdf").exists()


def test_cleanup_survives_missing_files(env):
    _, Session, storage, _ = env
    now = datetime.now(timezone.utc)
    seed_document(Session, storage, "d" * 64, now - timedelta(hours=1))
    storage.delete_prefix("documents/" + "d" * 64)
    with Session() as s:
        assert usage.cleanup_expired(s, storage, now) == 1


def test_an_upload_triggers_cleanup_of_expired_documents(env):
    client, Session, storage, _ = env
    expired = seed_document(Session, storage, "e" * 64, datetime.now(timezone.utc) - timedelta(hours=2))
    assert post(client).status_code == 200
    with Session() as s:
        assert "e" * 64 not in set(s.scalars(select(Document.sha256)))  # (SQLite reuses the freed id, so compare the hash)
        assert len(s.scalars(select(Document)).all()) == 1
    assert expired


def test_reuploading_extends_an_expiring_document_but_not_a_permanent_one(env):
    client, Session, _, _ = env
    assert post(client).status_code == 200
    with Session() as s:
        doc = s.scalar(select(Document))
        doc.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        s.commit()
    assert post(client).status_code == 200  # same file again
    with Session() as s:
        exp = s.scalar(select(Document)).expires_at
        exp = exp.replace(tzinfo=timezone.utc) if exp.tzinfo is None else exp
        assert exp - datetime.now(timezone.utc) > timedelta(hours=23)
        s.scalar(select(Document)).expires_at = None
        s.commit()
    assert post(client).status_code == 200
    with Session() as s:
        assert s.scalar(select(Document)).expires_at is None


def test_storage_delete_prefix_stays_inside_the_storage_directory(tmp_path):
    storage = LocalStorage(tmp_path / "store")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "keep.txt").write_text("x")
    with pytest.raises(ValueError):
        storage.delete_prefix("../outside")
    storage.delete_prefix("")  # the root itself is never removed
    assert storage.base_dir.exists() and (tmp_path / "outside" / "keep.txt").exists()
    storage.delete_prefix("missing/dir")  # absent is fine
    assert Path(storage.base_dir).exists()
