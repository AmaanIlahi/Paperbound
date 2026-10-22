"""Public uploads: on whenever a database and an OpenAI key are configured, off (samples only) otherwise; size and page
limits, per-IP rate limit, daily LLM budget in the database, and expiry of uploads."""

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from paperbound import config, deploy, usage
from paperbound.api.main import app
from paperbound.db.models import Document, Field, Job, Page, Run, RunEvent, UsageLedger
from paperbound.db.models import Base
from paperbound.db.session import get_session, session_factory
from paperbound.pipeline.extract import ExtractionResult
from paperbound.storage import LocalStorage
from tests.lease_fakes import fake_extractor
from tests.live import lease_pipeline, live  # noqa: F401


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


# ---- availability: samples always, uploads only with a database and a key ----

def test_uploads_need_a_database_and_an_openai_key(monkeypatch):
    def boom():
        raise AssertionError("the database must not be touched")

    app.dependency_overrides[get_session] = boom
    client = TestClient(app)
    try:
        for url, key in (("", "k"), ("sqlite://", ""), ("", "")):
            monkeypatch.setattr(config, "DATABASE_URL", url)
            monkeypatch.setattr(config, "OPENAI_API_KEY", key)
            response = client.post("/documents", files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
            assert response.status_code == 503 and "sample" in response.json()["detail"].lower(), (url, key)
            body = client.get("/config").json()
            assert body["uploads_enabled"] is False and body["unavailable_reason"] and body["budget_exhausted"] is False
        monkeypatch.setattr(config, "DATABASE_URL", "sqlite://")
        monkeypatch.setattr(config, "OPENAI_API_KEY", "k")
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        app.dependency_overrides[session_factory] = lambda: sessionmaker(engine)  # a reachable database
        assert client.get("/config").json()["uploads_enabled"] is True
    finally:
        app.dependency_overrides.clear()


def test_the_unavailable_reason_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    assert "database" in deploy.unavailable_reason()
    monkeypatch.setattr(config, "DATABASE_URL", "x")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    assert "OpenAI" in deploy.unavailable_reason()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "k")
    assert deploy.unavailable_reason() is None


def test_there_is_no_access_code_or_mode_switch_any_more(live):
    assert not hasattr(deploy, "access_code") and not hasattr(deploy, "live_enabled")
    assert live.upload().status_code == 200  # no header, no code: any visitor may upload
    assert live.upload(client=live.visitor()).status_code == 200


def test_a_missing_database_is_a_503_not_a_crash(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    assert TestClient(app).get("/runs/1").status_code == 503


def test_config_endpoint_describes_the_limits_and_the_privacy_note(live):
    body = live.client.get("/config").json()
    assert body["uploads_enabled"] is True and body["budget_exhausted"] is False and body["unavailable_reason"] is None
    assert body["max_upload_mb"] == 10 and body["max_pages"] == 20 and body["uploads_per_ip_per_hour"] == 5
    assert body["upload_ttl_hours"] == 24
    assert body["note"] == "Files are deleted after 24 hours. Don't upload sensitive documents."
    assert "access_code_required" not in body and "live_uploads_enabled" not in body


# ---- size and page limits ----

def test_file_over_10_mb_is_rejected(live):
    too_big = b"%PDF-1.4\n" + b"0" * (10 * 1024 * 1024)
    response = live.upload(too_big)
    assert response.status_code == 413 and "10 MB" in response.json()["detail"]
    with live.Session() as s:
        assert s.scalars(select(Document)).all() == []


def test_file_up_to_the_limit_is_read_and_checked_as_a_pdf(live):
    assert live.upload(b"%PDF-1.4\n" + b"0" * 1000).status_code == 400  # a PDF header but not parseable
    assert live.upload(b"not a pdf").status_code == 400


def test_more_than_20_pages_is_rejected_and_nothing_is_stored(live):
    assert live.upload(pdf_with_pages(21), ip="1.1.1.1").status_code == 413
    with live.Session() as s:
        assert s.scalars(select(Document)).all() == [] and s.scalars(select(Job)).all() == []
    assert not (live.storage.base_dir / "documents").exists()
    assert live.upload(pdf_with_pages(20), ip="1.1.1.1").status_code == 200


def test_limits_can_be_changed_with_environment_variables(live):
    live.monkeypatch.setenv("MAX_PAGES", "2")
    assert live.upload(pdf_with_pages(3)).status_code == 413
    live.monkeypatch.setenv("MAX_UPLOAD_MB", "0.001")
    assert live.upload(pdf_with_pages(1)).status_code == 413


# ---- rate limit: 5 uploads per IP per hour ----

def test_sixth_upload_from_one_address_within_an_hour_is_refused(live):
    for i in range(5):
        assert live.upload(ip="9.9.9.9").status_code == 200, i
    refused = live.upload(ip="9.9.9.9")
    assert refused.status_code == 429 and "5 per hour" in refused.json()["detail"]
    assert live.upload(ip="8.8.8.8").status_code == 200  # another address is unaffected
    with live.Session() as s:
        rows = s.scalars(select(UsageLedger).where(UsageLedger.kind == "upload")).all()
        assert len(rows) == 6 and all(r.ip_hash and "9.9.9.9" not in r.ip_hash for r in rows)  # hashes only


def test_the_limit_follows_the_address_not_the_session(live):
    for _ in range(5):
        assert live.upload(client=live.visitor(), ip="2.2.2.2").status_code == 200  # a new cookie each time
    assert live.upload(client=live.visitor(), ip="2.2.2.2").status_code == 429


def test_uploads_older_than_an_hour_do_not_count(live):
    old = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)
    with live.Session() as s:
        for _ in range(5):
            s.add(UsageLedger(kind="upload", ip_hash=usage.ip_hash("7.7.7.7"), created_at=old))
        s.commit()
    assert live.upload(ip="7.7.7.7").status_code == 200
    with live.Session() as s:
        s.add_all(UsageLedger(kind="upload", ip_hash=usage.ip_hash("6.6.6.6"), created_at=datetime.now(timezone.utc) - timedelta(minutes=59)) for _ in range(5))
        s.commit()
    assert live.upload(ip="6.6.6.6").status_code == 429


def test_rejected_files_still_count_against_the_limit(live):
    for _ in range(5):
        assert live.upload(b"not a pdf", ip="5.5.5.5").status_code == 400
    assert live.upload(ip="5.5.5.5").status_code == 429


def test_the_limit_is_configurable(live):
    live.monkeypatch.setenv("UPLOADS_PER_IP_PER_HOUR", "1")
    assert live.upload(ip="4.4.4.4").status_code == 200 and live.upload(ip="4.4.4.4").status_code == 429


def test_the_address_comes_from_fly_client_ip_else_the_socket(live):
    live.upload(ip="3.3.3.3")
    live.upload()  # no proxy header: the test client's own address
    with live.Session() as s:
        hashes = {r.ip_hash for r in s.scalars(select(UsageLedger).where(UsageLedger.kind == "upload"))}
    assert hashes == {usage.ip_hash("3.3.3.3"), usage.ip_hash("testclient")}


# ---- daily LLM budget, tracked in the database; /config reports it ----

def test_budget_blocks_uploads_once_spent_and_resets_the_next_day(live):
    live.monkeypatch.setenv("DAILY_LLM_BUDGET_USD", "0.50")
    live.use_pipeline(lease_pipeline(extractor=costly_extractor(0.40)))
    live.upload_and_run()  # spent 0.40, under 0.50
    assert live.client.get("/config").json()["budget_exhausted"] is False
    live.upload_and_run()  # the check happens before the run, so this one still starts
    assert live.client.get("/config").json()["budget_exhausted"] is True
    blocked = live.upload(ip="1.0.0.3")
    assert blocked.status_code == 429 and "daily limit" in blocked.json()["detail"].lower() and "$0.50" in blocked.json()["detail"]
    with live.Session() as s:
        assert usage.spent_today(s, datetime.now(timezone.utc)) == pytest.approx(0.80, abs=0.01)
        for row in s.scalars(select(UsageLedger).where(UsageLedger.kind == "llm")):  # the same spend, yesterday
            row.created_at = datetime.now(timezone.utc) - timedelta(days=1, minutes=1)
        s.commit()
    assert live.client.get("/config").json()["budget_exhausted"] is False
    assert live.upload(ip="1.0.0.4").status_code == 200


def test_samples_do_not_depend_on_the_budget(live):
    with live.Session() as s:
        s.add(UsageLedger(kind="llm", usd=5.0, created_at=datetime.now(timezone.utc)))
        s.commit()
    assert live.client.get("/config").json()["budget_exhausted"] is True
    assert live.client.get("/demo/fixtures").status_code == 200
    assert live.client.get("/demo/lease/events", params={"speed": 0}).status_code == 200


def test_default_budget_is_one_dollar(live):
    assert deploy.daily_llm_budget_usd() == 1.00


def test_spend_is_recorded_per_run_in_the_database(live):
    live.use_pipeline(lease_pipeline(extractor=costly_extractor(0.25)))
    run_id = live.upload_and_run()["run_id"]
    with live.Session() as s:
        run_cost = s.get(Run, run_id).cost_usd
        ledger = s.scalars(select(UsageLedger).where(UsageLedger.kind == "llm")).all()
    assert len(ledger) == 1 and ledger[0].usd == pytest.approx(run_cost) and run_cost >= 0.25


def test_a_blocked_upload_does_no_work(live):
    live.monkeypatch.setenv("DAILY_LLM_BUDGET_USD", "0.01")
    with live.Session() as s:
        s.add(UsageLedger(kind="llm", usd=0.5, created_at=datetime.now(timezone.utc)))
        s.commit()
    assert live.upload().status_code == 429
    with live.Session() as s:
        assert s.scalars(select(Document)).all() == [] and s.scalars(select(Job)).all() == []


# ---- uploads are deleted after 24 hours ----

def test_new_uploads_expire_after_24_hours(live):
    before = datetime.now(timezone.utc)
    assert live.upload().status_code == 200
    with live.Session() as s:
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
        s.add(Job(run_id=run.id, status="done", payload={}))
        s.add(RunEvent(run_id=run.id, seq=1, name="meta", data={}))
        s.commit()
        return doc.id


def test_cleanup_deletes_only_expired_documents_with_their_files_and_children(live):
    Session, storage = live.Session, live.storage
    now = datetime.now(timezone.utc)
    expired = seed_document(Session, storage, "a" * 64, now - timedelta(minutes=1))
    fresh = seed_document(Session, storage, "b" * 64, now + timedelta(hours=1))
    permanent = seed_document(Session, storage, "c" * 64, None)  # saved without an expiry: never touched
    with Session() as s:
        s.add(UsageLedger(kind="llm", usd=0.3, created_at=now))
        s.commit()
        assert usage.cleanup_expired(s, storage, now) == 1
        assert {d for d in s.scalars(select(Document.id))} == {fresh, permanent}
        assert s.scalars(select(Run).where(Run.document_id == expired)).all() == []
        # fields, pages, jobs and events of the expired document went with it
        assert len(s.scalars(select(Field)).all()) == 2 and len(s.scalars(select(Page)).all()) == 2
        assert len(s.scalars(select(Job)).all()) == 2 and len(s.scalars(select(RunEvent)).all()) == 2
        assert usage.spent_today(s, now) == pytest.approx(0.3)  # the budget survives the deletion
    assert not (storage.base_dir / "documents" / ("a" * 64)).exists()
    assert (storage.base_dir / "documents" / ("b" * 64) / "source.pdf").exists()
    assert (storage.base_dir / "documents" / ("c" * 64) / "source.pdf").exists()


def test_cleanup_survives_missing_files(live):
    now = datetime.now(timezone.utc)
    seed_document(live.Session, live.storage, "d" * 64, now - timedelta(hours=1))
    live.storage.delete_prefix("documents/" + "d" * 64)
    with live.Session() as s:
        assert usage.cleanup_expired(s, live.storage, now) == 1


def test_an_upload_triggers_cleanup_of_expired_documents(live):
    seed_document(live.Session, live.storage, "e" * 64, datetime.now(timezone.utc) - timedelta(hours=2))
    assert live.upload().status_code == 200
    with live.Session() as s:
        assert "e" * 64 not in set(s.scalars(select(Document.sha256)))  # (SQLite reuses the freed id, so compare the hash)
        assert len(s.scalars(select(Document)).all()) == 1


def test_reuploading_extends_an_expiring_document_but_not_a_permanent_one(live):
    assert live.upload().status_code == 200
    with live.Session() as s:
        doc = s.scalar(select(Document))
        doc.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        s.commit()
    assert live.upload().status_code == 200  # same file again
    with live.Session() as s:
        exp = s.scalar(select(Document)).expires_at
        exp = exp.replace(tzinfo=timezone.utc) if exp.tzinfo is None else exp
        assert exp - datetime.now(timezone.utc) > timedelta(hours=23)
        s.scalar(select(Document)).expires_at = None
        s.commit()
    assert live.upload().status_code == 200
    with live.Session() as s:
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


def test_queued_runs_count_against_the_budget_before_they_finish(live):
    """Runs are asynchronous: a burst of uploads must not all pass the check before the first one records its cost."""
    live.monkeypatch.setenv("DAILY_LLM_BUDGET_USD", "0.05")  # each unfinished run is assumed to cost 0.02
    codes = [live.upload(ip=f"10.0.0.{i}").status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]  # 0.00, 0.02, 0.04 are under 0.05; 0.06 is not
    assert live.client.get("/config").json()["budget_exhausted"] is True
    live.work()  # the real cost of fake runs is zero, so finishing them frees the allowance
    assert live.client.get("/config").json()["budget_exhausted"] is False
    assert live.upload(ip="10.0.0.9").status_code == 200


def test_the_allowance_per_unfinished_run_is_configurable(live):
    assert deploy.run_cost_estimate_usd() == 0.02
    live.monkeypatch.setenv("RUN_COST_ESTIMATE_USD", "0.5")
    live.monkeypatch.setenv("DAILY_LLM_BUDGET_USD", "0.5")
    assert live.upload(ip="10.1.0.1").status_code == 200 and live.upload(ip="10.1.0.2").status_code == 429
