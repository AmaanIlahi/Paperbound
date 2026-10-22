"""Session ownership: a run, its document, its files and its actions belong to the browser that uploaded them.
Every non-demo endpoint answers 404 to anyone else, whether they have another session, no cookie, or a forged one."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from paperbound import sessions
from paperbound.api.main import app
from paperbound.db.models import Document, Run
from tests.live import live  # noqa: F401


@pytest.fixture()
def owned(live):
    """The owner has uploaded the lease and its run has finished. Returns the ids everything else is probed with."""
    body = live.upload_and_run()
    run_id, doc_id = body["run_id"], body["document_id"]
    actions = live.client.get(f"/runs/{run_id}/actions").json()
    with live.Session() as s:
        sha = s.get(Document, doc_id).sha256
    return {"run": run_id, "doc": doc_id, "sha": sha, "reminder": actions[0]["id"], "email": actions[3]["id"]}


def forged_client() -> TestClient:
    client = TestClient(app)
    client.cookies.set(sessions.COOKIE_NAME, "someone-elses-token.0123456789abcdef")
    return client


def strangers(live):
    return {"another session": live.visitor(), "no cookie": TestClient(app), "forged cookie": forged_client()}


def reads(ids):
    return [
        f"/runs/{ids['run']}", f"/runs/{ids['run']}/steps", f"/runs/{ids['run']}/fields", f"/runs/{ids['run']}/obligations",
        f"/runs/{ids['run']}/brief", f"/runs/{ids['run']}/actions", f"/runs/{ids['run']}/events",
        f"/documents/{ids['doc']}/pages/1", f"/files/documents/{ids['sha']}/pages/1.png", f"/files/documents/{ids['sha']}/source.pdf",
    ]


def test_the_owner_can_read_every_endpoint(live, owned):
    for url in reads(owned):
        assert live.client.get(url).status_code == 200, url


def test_nobody_else_can_read_any_endpoint(live, owned):
    for who, client in strangers(live).items():
        for url in reads(owned):
            response = client.get(url)
            assert response.status_code == 404, (who, url, response.status_code)
            assert "detail" in response.json() and response.json()["detail"] in ("Run not found", "Document not found", "File not found")


def test_the_event_stream_does_not_leak_to_another_session(live, owned):
    body = live.client.get(f"/runs/{owned['run']}/events")
    assert body.status_code == 200 and "event: meta" in body.text
    for client in strangers(live).values():
        response = client.get(f"/runs/{owned['run']}/events")
        assert response.status_code == 404 and "event:" not in response.text


def test_nobody_else_can_approve_or_reject_and_nothing_changes(live, owned):
    for who, client in strangers(live).items():
        for verb in ("approve", "reject"):
            for key in ("reminder", "email"):
                response = client.post(f"/actions/{owned[key]}/{verb}")
                assert response.status_code == 404, (who, verb, key)
    actions = live.client.get(f"/runs/{owned['run']}/actions").json()
    assert {a["status"] for a in actions} == {"pending"}


def test_the_owner_can_still_decide(live, owned):
    assert live.client.post(f"/actions/{owned['reminder']}/approve").json()["status"] == "executed"
    assert live.client.post(f"/actions/{owned['email']}/reject").json()["status"] == "rejected"


def test_an_unknown_id_looks_the_same_as_someone_elses(live, owned):
    mine, theirs = live.client.get("/runs/9999"), live.visitor().get(f"/runs/{owned['run']}")
    assert mine.status_code == theirs.status_code == 404 and mine.json() == theirs.json()
    assert live.client.post("/actions/9999/approve").json() == live.visitor().post(f"/actions/{owned['reminder']}/approve").json()


def test_a_page_other_than_the_documents_own_is_not_reachable_through_a_path_trick(live, owned):
    for key in ("../x", f"documents/{owned['sha']}/../../secret", "documents/" + "0" * 64 + "/source.pdf", "somewhere/else.png"):
        assert live.client.get(f"/files/{key}").status_code == 404, key


def test_rows_from_before_sessions_existed_are_visible_to_nobody(live):
    with live.Session() as s:
        s.add(Document(id=50, filename="old.pdf", storage_key="k", sha256="f" * 64, page_count=1))
        s.add(Run(id=50, document_id=50, status="done"))  # no session_hash
        s.commit()
    for client in (live.client, TestClient(app)):
        assert client.get("/runs/50").status_code == 404 and client.get("/documents/50/pages/1").status_code == 404


def test_the_same_file_uploaded_by_two_visitors_gives_each_their_own_run(live):
    other = live.visitor()
    first = live.upload_and_run()
    second = live.upload_and_run(client=other)
    assert second["reused"] is True and first["document_id"] == second["document_id"] and first["run_id"] != second["run_id"]
    assert live.client.get(f"/runs/{first['run_id']}").status_code == 200
    assert live.client.get(f"/runs/{second['run_id']}").status_code == 404  # still not mine
    assert other.get(f"/runs/{first['run_id']}").status_code == 404
    assert other.get(f"/documents/{second['document_id']}/pages/1").status_code == 200  # a document is reachable through a run
    with live.Session() as s:
        hashes = {r.session_hash for r in s.scalars(select(Run))}
    assert len(hashes) == 2 and None not in hashes


# ---- the cookie itself ----

def test_first_visit_issues_a_signed_httponly_samesite_lax_cookie():
    response = TestClient(app).get("/")
    header = response.headers["set-cookie"]
    assert header.startswith(sessions.COOKIE_NAME + "=")
    assert "HttpOnly" in header and "SameSite=lax" in header and "Path=/" in header and "Max-Age=" in header
    assert "Secure" not in header  # plain http in tests
    assert "Secure" in TestClient(app, base_url="https://testserver").get("/").headers["set-cookie"]


def test_an_existing_valid_cookie_is_kept_not_reissued():
    client = TestClient(app)
    client.get("/")
    assert "set-cookie" not in client.get("/").headers


def test_upload_without_a_cookie_creates_the_session_and_owns_the_run(live):
    fresh = TestClient(app)
    response = live.upload(client=fresh)
    assert response.status_code == 200 and sessions.COOKIE_NAME in response.headers["set-cookie"]
    live.work()
    assert fresh.get(f"/runs/{response.json()['run_id']}").status_code == 200


def test_only_a_hash_of_the_token_is_stored(live, owned):
    token = live.cookie.rpartition(".")[0]
    with live.Session() as s:
        stored = {r.session_hash for r in s.scalars(select(Run))} | {d.session_hash for d in s.scalars(select(Document))}
    assert stored == {live.hash} and token not in stored and live.cookie not in stored


def test_cookie_signature_is_checked(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "one")
    value, hashed = sessions.new_cookie()
    assert sessions.read_cookie(value) == hashed
    token, _, signature = value.rpartition(".")
    for bad in (None, "", "nodot", f"{token}.", f".{signature}", f"{token}x.{signature}", f"{token}.{signature[:-1]}{'1' if signature[-1] == '0' else '0'}",
                f"{token}.{'0' * len(signature)}"):
        assert sessions.read_cookie(bad) is None, bad
    monkeypatch.setenv("SESSION_SECRET", "two")  # rotating the secret logs everyone out
    assert sessions.read_cookie(value) is None
