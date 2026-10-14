"""Local Svelte UI over Papertrail's document sidecars and reconciliation state."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import socket
import tempfile
import threading
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Literal

import fitz
import openpyxl
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from papertrail.reconciliation_groundtruth import (
    approval_map,
    document_sets_match,
    groundtruth_path_for_document,
    load_groundtruth,
    rows_with_transaction_keys,
    transaction_key_id,
)
from tools import review
from tools.shared import find_companion, get_export_dir, get_processed_dir, iter_sidecars


class Decision(BaseModel):
    action: Literal["approve", "reject", "clear"]
    revision: str


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def label(value: object, fallback: str = "Unknown") -> str:
    return str(value) if value and value != "$UNKNOWN$" else fallback


def atomic_save(path: Path, data: dict) -> None:
    """Preserve all groundtruth keys and replace a complete file atomically."""
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class DashboardStore:
    def __init__(self, processed: Path | None, export: Path | None):
        self.processed = processed.resolve() if processed else None
        self.export = export.resolve() if export else None
        self.roots = [p for p in (self.processed, self.export) if p]
        self.lock = threading.RLock()
        self.documents: dict[str, dict] = {}
        self.rows: dict[str, dict] = {}

    def contain(self, path: Path) -> Path:
        """Prevent symlink access outside the explicitly selected profile."""
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in self.roots):
            raise HTTPException(403, "Document is outside the selected profile.")
        return path

    def add_document(self, path: Path, meta: dict) -> str:
        path = self.contain(path)
        identity = digest(meta.get("hash_file") or str(path))[:24]
        # Prefer processed originals over identical exported copies.
        if identity in self.documents:
            return identity
        self.documents[identity] = {
            "id": identity,
            "_path": path,
            "date": meta.get("date_issued") or "",
            "title": label(meta.get("document_title"), path.stem),
            "issuer": label(meta.get("issuing_party_raw"), label(meta.get("issuing_party"))),
            "type": label(meta.get("document_type_raw"), label(meta.get("document_type"))),
            "type_key": meta.get("document_type") or "unknown",
            "amount": meta.get("total_amount"),
            "currency": meta.get("total_amount_currency") or "EUR",
            "filename": path.name,
            "matches": [],
        }
        return identity

    def add_transaction(self, month: str, statement: dict, row: dict, data: dict) -> None:
        files = []
        index = {**data["cross_file_index"], **data["file_index"]}
        for name in row.get("files", []):
            entry = index.get(name)
            # Missing evidence is retained on the transaction instead of hidden.
            if entry and entry.get("doc_path"):
                files.append(self.add_document(Path(entry["doc_path"]), entry["metadata"]))
        source = self.contain(Path(statement["doc_path"]))
        path = self.contain(groundtruth_path_for_document(source))
        groundtruth = load_groundtruth(path)
        identities = review._current_documents(row.get("files", []), data)
        key = row["_transaction_key_id"]
        approved = approval_map(groundtruth).get(key)
        rejected = next(
            (
                a
                for a in groundtruth.get("rejections", [])
                if transaction_key_id(a["transaction"]) == key
            ),
            None,
        )
        status = ""
        # Approval validity follows the same document identity rules as the legacy UI.
        if approved and document_sets_match(
            identities, approved["required_documents"], strict_hash=True
        ):
            status = "approved"
        elif rejected and document_sets_match(
            identities, rejected["required_documents"], strict_hash=True
        ):
            status = "rejected"
        elif approved or rejected:
            status = "changed"
        identity = digest([str(source), key])[:24]
        self.rows[identity] = {
            "id": identity,
            "month": month,
            "date": row.get("date") or "",
            "description": row.get("description") or "Transaction",
            "amount": row.get("amount"),
            "currency": row.get("currency") or "EUR",
            "files": files,
            "status": status,
            "missing_files": [n for n in row.get("files", []) if n not in index],
            "warnings": row.get("warnings", []) + row.get("errors", []),
            "reason": row.get("reasoning") or "",
            "revision": digest([row, identities, groundtruth]),
            "_path": path,
            "_source": source.name,
            "_row": row,
            "_identities": identities,
        }
        for file_id in files:
            self.documents[file_id]["matches"].append(identity)

    def scan_month(self, month: str, folder: Path) -> None:
        data, _ = review.load_export_folder(str(folder), self.export)
        for entry in data["file_index"].values():
            self.add_document(Path(entry["doc_path"]), entry["metadata"])
        for statement in data["bank_statements"]:
            for row in rows_with_transaction_keys(statement.get("reconciliation") or {}):
                self.add_transaction(month or (row.get("date") or "")[:7], statement, row, data)

    def snapshot(self) -> dict:
        """Reload disk state and strip internal paths before responding."""
        self.documents, self.rows = {}, {}
        errors = []
        # A profile can have only processed documents or only monthly exports.
        if self.processed and self.processed.is_dir():
            for sidecar, meta in iter_sidecars(self.processed):
                path = find_companion(sidecar, meta)
                if path and isinstance(meta, dict):
                    self.contain(sidecar)
                    self.add_document(path, meta)
        base, months, _ = (
            review._export_folder_options(self.export) if self.export else (None, [], None)
        )
        export_months = [m for m in months if re.fullmatch(r"\d{4}-\d{2}", m)]
        for month in months:
            try:
                self.scan_month(month if month in export_months else "", self.contain(base / month))
            except (OSError, ValueError, KeyError, TypeError):
                errors.append(f"Could not read reconciliation for {month}.")
        months = sorted(
            set(export_months)
            | {r["month"] for r in self.rows.values() if r["month"]}
            | {d["date"][:7] for d in self.documents.values() if len(d["date"]) >= 7},
            reverse=True,
        )
        documents = [
            {k: v for k, v in d.items() if not k.startswith("_")} for d in self.documents.values()
        ]
        documents.sort(key=lambda d: (d["date"], d["title"]), reverse=True)
        return {
            "documents": documents,
            "transactions": [
                {k: v for k, v in r.items() if not k.startswith("_")} for r in self.rows.values()
            ],
            "months": months,
            "export_months": export_months,
            "errors": errors,
        }

    def document(self, identity: str) -> dict:
        if not self.documents:
            self.snapshot()
        doc = self.documents.get(identity)
        if doc is None:
            raise HTTPException(404, "Document no longer exists. Refresh the dashboard.")
        self.contain(doc["_path"])
        return doc

    def decide(self, identity: str, command: Decision) -> dict:
        self.snapshot()
        entry = self.rows.get(identity)
        # Fail before writing if the transaction or evidence has changed.
        if entry is None:
            raise HTTPException(404, "Transaction no longer exists.")
        if command.revision != entry["revision"]:
            raise HTTPException(409, "This match changed. Refresh and review it again.")
        if command.action != "clear" and (not entry["files"] or entry["missing_files"]):
            raise HTTPException(422, "All matched documents must be available before review.")
        path = self.contain(entry["_path"])
        data = load_groundtruth(path)
        row = entry["_row"]
        for collection in ("approvals", "rejections"):
            data[collection] = [
                a
                for a in data.get(collection, [])
                if transaction_key_id(a["transaction"]) != row["_transaction_key_id"]
            ]
        # Undo clears only this transaction; unrelated approval keys remain intact.
        if command.action != "clear":
            collection = "approvals" if command.action == "approve" else "rejections"
            data[collection].append(
                {
                    "confirmed_at": datetime.now().isoformat(timespec="seconds"),
                    "transaction": {**row["_transaction_key"], "description": row["description"]},
                    "required_documents": entry["_identities"],
                    "source_hint": {"statement_file": entry["_source"], "row": row["row"]},
                }
            )
        data["source"] = entry["_source"]
        data["updated"] = datetime.now().isoformat(timespec="seconds")
        atomic_save(path, data)
        return self.snapshot()


def create_app(processed: Path | None, export: Path | None) -> FastAPI:
    app = FastAPI(title="Papertrail", docs_url=None, redoc_url=None)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    store = DashboardStore(processed, export)
    token = secrets.token_urlsafe(32)
    app.state.store = store

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Local decisions require a token readable only through the same-origin API.
        if request.method == "POST" and request.headers.get("x-papertrail-token") != token:
            return Response("Invalid review token", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/api/state")
    def state():
        with store.lock:
            return {**store.snapshot(), "token": token}

    @app.post("/api/transactions/{identity}/decision")
    def decide(identity: str, command: Decision):
        with store.lock:
            return {**store.decide(identity, command), "token": token}

    @app.get("/api/documents/{identity}/file")
    def file(identity: str, download: bool = False):
        with store.lock:
            doc = store.document(identity)
            if doc["_path"].suffix.lower() not in {".pdf", ".xlsx"}:
                raise HTTPException(422, "Only PDF and XLSX files can be opened.")
            return FileResponse(
                doc["_path"],
                filename=doc["filename"],
                content_disposition_type="attachment" if download else "inline",
            )

    @app.get("/api/documents/{identity}/preview")
    def preview(identity: str, page: int = 0):
        with store.lock:
            path = store.document(identity)["_path"]
            if path.suffix.lower() == ".xlsx":
                book = openpyxl.load_workbook(path, data_only=True, read_only=True)
                try:
                    from itertools import islice

                    rows = [
                        ["" if v is None else str(v) for v in row]
                        for row in islice(book.active.iter_rows(values_only=True), 100)
                    ]
                    return {"kind": "sheet", "rows": rows, "pages": 1}
                finally:
                    book.close()
            if path.suffix.lower() != ".pdf":
                raise HTTPException(422, "Preview supports PDF and XLSX documents.")
            with fitz.open(path) as pdf:
                if page < 0 or page >= len(pdf):
                    raise HTTPException(404, "Page is unavailable.")
                return {
                    "kind": "pdf",
                    "pages": len(pdf),
                    "page": page,
                    "image": f"/api/documents/{identity}/image?page={page}",
                }

    @app.get("/api/documents/{identity}/image")
    def image(identity: str, page: int = 0):
        with store.lock:
            path = store.document(identity)["_path"]
            if path.suffix.lower() != ".pdf":
                raise HTTPException(422, "This document is not a PDF.")
            with fitz.open(path) as pdf:
                if page < 0 or page >= len(pdf):
                    raise HTTPException(404, "Page is unavailable.")
                return Response(
                    pdf[page].get_pixmap(dpi=120).tobytes("png"), media_type="image/png"
                )

    @app.get("/api/export/{month}")
    def export_month(month: str):
        with store.lock:
            base, choices, _ = (
                review._export_folder_options(store.export) if store.export else (None, [], None)
            )
            if month not in choices:
                raise HTTPException(404, "Export month is unavailable.")
            folder = store.contain(base / month)
            # Each response owns its archive; parallel profiles cannot share ZIP bytes.
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
                archive_path = Path(handle.name)
            try:
                with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                    for path in sorted(folder.rglob("*")):
                        if path.is_file() and not path.is_symlink():
                            store.contain(path)
                            archive.write(path, Path(month) / path.relative_to(folder))
            except Exception:
                archive_path.unlink(missing_ok=True)
                raise
            return FileResponse(
                archive_path,
                filename=f"{month}.zip",
                media_type="application/zip",
                background=BackgroundTask(archive_path.unlink, missing_ok=True),
            )

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static" / "dashboard", html=True))
    return app


def launch(
    *,
    processed_path: Path | None = None,
    export_path: Path | None = None,
    argv: list[str] | None = None,
    open_browser: bool = True,
) -> None:
    """Use bundled assets and a loopback listener, with an OS-assigned auto port."""
    parser = argparse.ArgumentParser(description="Papertrail Svelte dashboard")
    parser.add_argument("--port", default="auto")
    args = parser.parse_args(argv)
    processed = processed_path or (Path(get_processed_dir()) if get_processed_dir() else None)
    export = export_path or (Path(get_export_dir()) if get_export_dir() else None)
    port = 0 if args.port == "auto" else int(args.port)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        print(f"Papertrail dashboard: {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        server = uvicorn.Server(uvicorn.Config(create_app(processed, export), log_level="warning"))
        server.run(sockets=[listener])


if __name__ == "__main__":
    launch()
