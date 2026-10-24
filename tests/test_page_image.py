"""A document is shared by its sha256, but its files live in the storage that saved them. A re-upload must heal a
document whose files are missing from this storage (the database reached from another place, a restored volume)."""

from tests.live import live  # noqa: F401


def image_status(live, run_id):
    run = live.client.get(f"/runs/{run_id}").json()
    page = live.client.get(f"/documents/{run['document_id']}/pages/1").json()
    return live.client.get(page["image_url"]).status_code


def test_reuploading_a_document_whose_files_are_missing_restores_them(live):
    first = live.upload_and_run()
    assert image_status(live, first["run_id"]) == 200
    document = live.client.get(f"/runs/{first['run_id']}").json()["document_id"]
    sha = live.Session().execute(__import__("sqlalchemy").text("select sha256 from documents where id = :i"), {"i": document}).scalar()

    live.storage.delete_prefix(f"documents/{sha}")  # the row survives, its files do not
    assert image_status(live, first["run_id"]) == 404

    second = live.upload_and_run()  # same bytes: the document is reused, not re-parsed from scratch
    assert second["document_id"] == first["document_id"] and second["reused"] is True
    assert image_status(live, second["run_id"]) == 200
    assert live.storage.exists(f"documents/{sha}/source.pdf")


def test_a_reupload_with_intact_files_writes_nothing(live, monkeypatch):
    live.upload_and_run()
    from paperbound.api import routes

    monkeypatch.setattr(routes, "parse_pdf", lambda data: (_ for _ in ()).throw(AssertionError("must not re-parse")))
    assert live.upload().json()["reused"] is True
