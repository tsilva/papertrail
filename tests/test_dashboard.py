import json

import pytest
from fastapi.testclient import TestClient

from tests.dashboard_fixtures import make_dashboard_fixture
from tools.dashboard import create_app


@pytest.fixture
def client(tmp_path):
    processed, export = make_dashboard_fixture(tmp_path)
    with TestClient(create_app(processed, export)) as client:
        yield client


def command(client, row, action, token):
    return client.post(
        f"/api/transactions/{row['id']}/decision",
        json={"action": action, "revision": row["revision"]},
        headers={"x-papertrail-token": token},
    )


def test_documents_deduplicate_export_copies_and_serve_sources(client):
    state = client.get("/api/state").json()
    assert len(state["documents"]) == 6
    assert len(state["transactions"]) == 6
    assert "_path" not in json.dumps(state)
    doc = next(d for d in state["documents"] if d["title"] == "Invoice INV-2048")
    assert len(doc["matches"]) == 1
    preview = client.get(f"/api/documents/{doc['id']}/preview").json()
    assert preview["pages"] == 1
    assert client.get(preview["image"]).headers["content-type"] == "image/png"
    assert client.get(f"/api/documents/{doc['id']}/preview?page=1").status_code == 404
    file = client.get(f"/api/documents/{doc['id']}/file?download=true")
    assert "attachment" in file.headers["content-disposition"]
    assert file.content.startswith(b"%PDF")
    assert client.get("/api/documents/arbitrary-path/file").status_code == 404


def test_approve_reject_clear_persist_and_preserve_other_groundtruth(client):
    state = client.get("/api/state").json()
    row = state["transactions"][0]
    path = client.app.state.store.rows[row["id"]]["_path"]
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "approvals": [],
                "unmatched_file_approvals": [{"document": {"filename": "expected.pdf"}}],
            }
        )
    )
    state = client.get("/api/state").json()
    row = state["transactions"][0]
    approved = command(client, row, "approve", state["token"])
    assert approved.status_code == 200
    data = approved.json()
    updated = next(t for t in data["transactions"] if t["id"] == row["id"])
    assert updated["status"] == "approved"
    assert len(json.loads(path.read_text())["approvals"]) == 1
    rejected = command(client, updated, "reject", data["token"]).json()
    updated = next(t for t in rejected["transactions"] if t["id"] == row["id"])
    assert updated["status"] == "rejected"
    persisted = json.loads(path.read_text())
    assert persisted["approvals"] == []
    assert len(persisted["rejections"]) == 1
    cleared = command(client, updated, "clear", data["token"]).json()
    assert next(t for t in cleared["transactions"] if t["id"] == row["id"])["status"] == ""
    assert (
        json.loads(path.read_text())["unmatched_file_approvals"][0]["document"]["filename"]
        == "expected.pdf"
    )


def test_stale_requests_and_missing_evidence_never_write(client):
    data = client.get("/api/state").json()
    row = data["transactions"][0]
    assert command(client, row, "approve", data["token"]).status_code == 200
    assert command(client, row, "reject", data["token"]).status_code == 409
    missing = next(t for t in data["transactions"] if not t["files"])
    fresh = client.get("/api/state").json()
    missing = next(t for t in fresh["transactions"] if t["id"] == missing["id"])
    assert command(client, missing, "approve", data["token"]).status_code == 422
    assert command(client, missing, "approve", "invalid").status_code == 403


def test_changed_document_hash_invalidates_prior_approval(client):
    data = client.get("/api/state").json()
    row = data["transactions"][0]
    assert command(client, row, "approve", data["token"]).status_code == 200
    # Rename too, so neither persisted hash nor fallback filename can match.
    export = client.app.state.store.export / "2026-10"
    old = row["files"][0]
    filename = client.app.state.store.documents[old]["filename"]
    old_path = export / filename
    new_path = export / "changed.pdf"
    old_path.rename(new_path)
    meta = json.loads(old_path.with_suffix(".json").read_text())
    meta.update(hash_file="changed-file", hash_content="changed-content")
    old_path.with_suffix(".json").unlink()
    new_path.with_suffix(".json").write_text(json.dumps(meta))
    recon_path = export / "statement.reconciliation.json"
    recon = json.loads(recon_path.read_text())
    recon["matches"][0]["files"] = ["changed.pdf"]
    recon_path.write_text(json.dumps(recon))
    fresh = client.get("/api/state").json()
    assert fresh["transactions"][0]["status"] == "changed"
    assert command(client, row, "approve", data["token"]).status_code == 409


def test_spreadsheet_and_month_export(client):
    data = client.get("/api/state").json()
    doc = next(d for d in data["documents"] if d["type_key"] == "bank-statement")
    preview = client.get(f"/api/documents/{doc['id']}/preview").json()
    assert preview["kind"] == "sheet"
    assert preview["rows"][0] == ["Date", "Transaction", "Amount"]
    archive = client.get("/api/export/2026-10")
    assert archive.status_code == 200
    assert archive.content.startswith(b"PK")
    assert client.get("/api/export/2099-01").status_code == 404


def test_document_symlink_cannot_escape_profile(client, tmp_path):
    data = client.get("/api/state").json()
    doc = data["documents"][0]
    path = client.app.state.store.documents[doc["id"]]["_path"]
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    assert client.get(f"/api/documents/{doc['id']}/file").status_code == 403


def test_empty_profile_serves_dashboard(tmp_path):
    with TestClient(create_app(tmp_path, None)) as client:
        assert client.get("/api/state").json()["documents"] == []
        assert client.get("/").status_code == 200


def test_same_filename_with_changed_hash_requires_review(client):
    data = client.get("/api/state").json()
    row = data["transactions"][0]
    assert command(client, row, "approve", data["token"]).status_code == 200
    export = client.app.state.store.export / "2026-10"
    filename = client.app.state.store.documents[row["files"][0]]["filename"]
    sidecar = (export / filename).with_suffix(".json")
    metadata = json.loads(sidecar.read_text())
    metadata.update(hash_file="replacement-file", hash_content="replacement-content")
    sidecar.write_text(json.dumps(metadata))
    fresh = client.get("/api/state").json()
    assert fresh["transactions"][0]["status"] == "changed"


def test_multiple_matched_documents_and_pdf_pages(client):
    store = client.app.state.store
    data = client.get("/api/state").json()
    row = data["transactions"][0]
    export = store.export / "2026-10"
    recon_path = export / "statement.reconciliation.json"
    recon = json.loads(recon_path.read_text())
    recon["matches"][0]["files"].append("inv-6615.pdf")
    recon_path.write_text(json.dumps(recon))
    data = client.get("/api/state").json()
    row = data["transactions"][0]
    assert len(row["files"]) == 2
    assert command(client, row, "approve", data["token"]).status_code == 200
    assert (
        len(
            json.loads(store.rows[row["id"]]["_path"].read_text())["approvals"][0][
                "required_documents"
            ]
        )
        == 2
    )
    import fitz

    path = store.documents[row["files"][0]]["_path"]
    with fitz.open(path) as pdf:
        pdf.new_page()
        pdf.saveIncr()
    preview = client.get(f"/api/documents/{row['files'][0]}/preview?page=1").json()
    assert preview["pages"] == 2
    assert preview["page"] == 1
    assert client.get(preview["image"]).status_code == 200


def test_empty_export_root_does_not_hide_processed_documents(tmp_path):
    processed, export = make_dashboard_fixture(tmp_path)
    import shutil

    shutil.rmtree(export / "2026-10")
    with TestClient(create_app(processed, export)) as client:
        data = client.get("/api/state").json()
    assert data["months"] == ["2026-10"]
    assert data["export_months"] == []
    assert len(data["documents"]) == 5
