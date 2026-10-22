"""Hourly cleanup setting, the database health check, and the fallback when the database is unreachable."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from paperbound import config, deploy, usage
from paperbound.api.main import app
from paperbound.db.session import session_factory
from tests.live import live  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def unreachable(*args, **kwargs):
    raise OperationalError("SELECT 1", {}, Exception("connection refused"))


# ---- cleanup interval ----

def test_cleanup_runs_every_hour_unless_configured(monkeypatch):
    monkeypatch.delenv("CLEANUP_INTERVAL_MINUTES", raising=False)
    assert deploy.cleanup_interval_seconds() == 3600
    monkeypatch.setenv("CLEANUP_INTERVAL_MINUTES", "15")
    assert deploy.cleanup_interval_seconds() == 900
    monkeypatch.setenv("CLEANUP_INTERVAL_MINUTES", "0.5")
    assert deploy.cleanup_interval_seconds() == 30


@pytest.mark.parametrize("bad", ["", "soon", "0", "-5"])
def test_a_bad_cleanup_interval_means_one_hour(monkeypatch, bad):
    monkeypatch.setenv("CLEANUP_INTERVAL_MINUTES", bad)
    assert deploy.cleanup_interval_seconds() == 3600


def test_the_background_loop_sleeps_for_the_configured_interval(monkeypatch):
    import asyncio

    from paperbound.api import main

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setenv("CLEANUP_INTERVAL_MINUTES", "7")
    monkeypatch.setattr(main, "_cleanup_once", lambda: None)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._cleanup_loop())
    assert sleeps == [7 * 60]
    assert not hasattr(main, "CLEANUP_INTERVAL_SECONDS")


# ---- /healthz/db ----

def test_healthz_db_is_200_when_the_database_answers(live):
    response = live.client.get("/healthz/db")
    assert response.status_code == 200 and response.json() == {"ok": True, "database": "up"}


def test_healthz_db_is_503_when_the_database_is_down(live):
    class Broken:
        def __enter__(self):
            raise OperationalError("connect", {}, Exception("connection refused"))

        def __exit__(self, *exc):
            return False

    app.dependency_overrides[session_factory] = lambda: Broken
    response = live.client.get("/healthz/db")
    assert response.status_code == 503 and response.json()["ok"] is False


def test_healthz_db_is_503_with_no_database_configured(monkeypatch):
    app.dependency_overrides.clear()
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr("paperbound.db.session._engine", None)
    assert TestClient(app).get("/healthz/db").status_code == 503


def test_plain_healthz_never_touches_the_database(monkeypatch):
    def boom():
        raise AssertionError("/healthz must not use the database")

    app.dependency_overrides[session_factory] = boom
    try:
        response = TestClient(app).get("/healthz")
        assert response.status_code == 200 and response.json() == {"ok": True}
    finally:
        app.dependency_overrides.clear()


# ---- graceful fallback ----

def test_config_offers_samples_only_while_the_database_is_unreachable(live):
    def broken_factory():
        raise OperationalError("connect", {}, Exception("connection refused"))

    app.dependency_overrides[session_factory] = lambda: broken_factory
    body = live.client.get("/config").json()
    assert body["uploads_enabled"] is False and body["temporarily_unavailable"] is True
    assert body["unavailable_reason"] == "Uploads are temporarily unavailable, try a sample"
    assert body["budget_exhausted"] is False
    assert "Traceback" not in str(body)


def test_config_is_normal_when_the_database_is_up(live):
    body = live.client.get("/config").json()
    assert body["uploads_enabled"] is True and body["temporarily_unavailable"] is False


def test_an_upload_while_the_database_is_down_is_a_polite_503(live, monkeypatch):
    monkeypatch.setattr(usage, "cleanup_expired", unreachable)
    response = live.upload()
    assert response.status_code == 503
    assert response.json()["detail"] == "Uploads are temporarily unavailable, try a sample."
    assert live.client.get("/demo/fixtures").status_code == 200  # samples do not need the database


# ---- keep-alive workflow ----

def test_the_keepalive_workflow_pings_the_database_check_daily():
    text = (ROOT / ".github" / "workflows" / "keepalive.yml").read_text(encoding="utf-8")
    assert 'cron: "0 14 * * *"' in text and "workflow_dispatch" in text
    assert "/healthz/db" in text and "curl -fsS" in text and "fly.toml" in text
    app_name = re.search(r"""^app\s*=\s*['"]([^'"]+)['"]""", (ROOT / "fly.toml").read_text(encoding="utf-8"), re.M)
    assert app_name, "fly.toml names the app the workflow derives its URL from"
