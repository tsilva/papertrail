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
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import fitz
import openpyxl
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from papertrail.reconciliation_groundtruth import (
    approval_map,
    document_matches_approval,
    document_sets_match,
    groundtruth_path_for_document,
    load_groundtruth,
    rows_with_transaction_keys,
    transaction_key_id,
)
from tools import review
from tools.shared import find_companion, get_export_dir, get_processed_dir, iter_sidecars


class UnmatchedDecision(BaseModel):
    expected: bool
    revision: str


class Decision(BaseModel):
    action: Literal["approve", "reject", "clear"]
    revision: str


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def label(value: object, fallback: str = "Unknown") -> str:
    return str(value) if value and value != "$UNKNOWN$" else fallback


def calendar_month(value: object, *, full_date: bool = False) -> str:
    """Exclude unknown markers and invalid calendar values from month filters."""
    if not isinstance(value, str):
        return ""
    pattern = r"\d{4}-\d{2}-\d{2}" if full_date else r"\d{4}-\d{2}"
    if not re.fullmatch(pattern, value):
        return ""
    try:
        date.fromisoformat(value if full_date else f"{value}-01")
    except ValueError:
        return ""
    return value[:7]


def open_pdf(path: Path) -> fitz.Document:
    """Turn unreadable source evidence into a recoverable dashboard error."""
    try:
        pdf = fitz.open(path)
    except (fitz.FileDataError, fitz.EmptyFileError, OSError) as exc:
        raise HTTPException(
            422, "This PDF cannot be read. Restore the source file and try again."
        ) from exc
    if pdf.needs_pass:
        pdf.close()
        raise HTTPException(
            422, "This PDF is password-protected. Unlock the source file and try again."
        )
    return pdf


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
        self.statements: dict[str, dict] = {}
        self.unmatched: dict[str, dict] = {}

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
            "_metadata": meta,
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
            "statement_id": statement["_dashboard_id"],
            "statement_document": statement["_dashboard_document"],
            "bank": label(
                statement["metadata"].get("issuing_party_raw"),
                label(statement["metadata"].get("issuing_party")),
            ),
            "account": statement["metadata"].get("bank_statement", {}).get("account_number") or "",
            "row_number": row.get("row"),
            "category": row.get("transaction_category") or "",
            "method": row.get("method") or "",
            "confidence": row.get("confidence"),
            "match_status": "unclassified"
            if row.get("transaction_category") == "unclassified"
            else "unmatched"
            if not row.get("files")
            else review._match_status(row),
            "file_names": row.get("files", []),
            "date": row.get("date") or "",
            "description": row.get("description") or "Transaction",
            "amount": row.get("amount"),
            "currency": row.get("currency") or "EUR",
            "files": files,
            "status": status,
            "missing_files": [
                n for n in row.get("files", []) if n not in index or not index[n].get("doc_path")
            ],
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
            meta = statement["metadata"]
            bank = meta.get("bank_statement") or {}
            source = Path(statement["doc_path"]) if statement.get("doc_path") else None
            identity = digest([month, statement["json_path"]])[:24]
            document_id = self.add_document(source, meta) if source else ""
            statement["_dashboard_id"] = identity
            statement["_dashboard_document"] = document_id
            rows = rows_with_transaction_keys(statement.get("reconciliation") or {})
            self.statements[identity] = {
                "id": identity,
                "month": month,
                "document_id": document_id,
                "bank": label(meta.get("issuing_party_raw"), label(meta.get("issuing_party"))),
                "account": bank.get("account_number") or "",
                "period_start": bank.get("period_start") or "",
                "period_end": bank.get("period_end") or "",
                "filename": source.name if source else Path(statement["json_path"]).name,
                "has_reconciliation": bool(statement.get("reconciliation")),
                "transaction_count": len(rows)
                if statement.get("reconciliation")
                else bank.get("transaction_count", 0),
            }
            for row in rows:
                self.add_transaction(month or (row.get("date") or "")[:7], statement, row, data)
        # Preserve the legacy month-wide unmatched list and its owning statement.
        for item in data["unmatched_files"]:
            statement = review._find_bank_statement(data, item["_statement_file"])
            if not statement or not statement.get("doc_path"):
                continue
            document = review._document_identity(item.get("file", ""), data)
            index = {**data["cross_file_index"], **data["file_index"]}
            evidence = index.get(item.get("file", ""))
            document_id = (
                self.add_document(Path(evidence["doc_path"]), evidence["metadata"])
                if evidence and evidence.get("doc_path")
                else ""
            )
            path = self.contain(groundtruth_path_for_document(Path(statement["doc_path"])))
            groundtruth = load_groundtruth(path)
            saved = next(
                (
                    a
                    for a in groundtruth.get("unmatched_file_approvals", [])
                    if document_matches_approval(document, a.get("document", {}), strict_hash=True)
                ),
                None,
            )
            identity = digest([month, str(path), item.get("file")])[:24]
            self.unmatched[identity] = {
                "id": identity,
                "month": month,
                "document_id": document_id,
                "filename": item.get("file", ""),
                "expected": bool(saved),
                "revision": digest([item, document, groundtruth]),
                "_path": path,
                "_document": document,
                "_source": item["_statement_file"],
            }

    def snapshot(self) -> dict:
        """Reload disk state and strip internal paths before responding."""
        self.documents, self.rows, self.statements, self.unmatched = {}, {}, {}, {}
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
        export_months = [m for m in months if calendar_month(m)]
        for month in months:
            try:
                self.scan_month(month if month in export_months else "", self.contain(base / month))
            except (OSError, ValueError, KeyError, TypeError):
                errors.append(f"Could not read reconciliation for {month}.")
        months = sorted(
            set(export_months)
            | {m for r in self.rows.values() if (m := calendar_month(r["month"]))}
            | {
                m
                for d in self.documents.values()
                if (m := calendar_month(d["date"], full_date=True))
            },
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
            "statements": list(self.statements.values()),
            "unmatched_documents": [
                {k: v for k, v in d.items() if not k.startswith("_")}
                for d in self.unmatched.values()
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
        if not doc["_path"].is_file():
            raise HTTPException(
                404,
                "Source file is unavailable. Restore it and try again, or refresh the dashboard.",
            )
        return doc

    def decide_unmatched(self, identity: str, command: UnmatchedDecision) -> dict:
        self.snapshot()
        entry = self.unmatched.get(identity)
        if entry is None:
            raise HTTPException(404, "Unmatched document no longer exists.")
        if command.revision != entry["revision"]:
            raise HTTPException(409, "This document changed. Refresh and inspect it again.")
        if command.expected and not entry["document_id"]:
            raise HTTPException(422, "The document must be available before review.")
        data = load_groundtruth(entry["_path"])
        data["unmatched_file_approvals"] = [
            a
            for a in data.get("unmatched_file_approvals", [])
            if not document_matches_approval(entry["_document"], a.get("document", {}))
        ]
        if command.expected:
            data["unmatched_file_approvals"].append(
                {
                    "confirmed_at": datetime.now().isoformat(timespec="seconds"),
                    "document": entry["_document"],
                    "status": "expected_unreconciled",
                    "source_hint": {"statement_file": entry["_source"]},
                }
            )
        data["source"] = entry["_source"]
        data["updated"] = datetime.now().isoformat(timespec="seconds")
        atomic_save(entry["_path"], data)
        return self.snapshot()

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

    @app.post("/api/unmatched/{identity}/decision")
    def decide_unmatched(identity: str, command: UnmatchedDecision):
        with store.lock:
            return {**store.decide_unmatched(identity, command), "token": token}

    @app.get("/api/documents/{identity}/metadata")
    def metadata(identity: str):
        with store.lock:
            return store.document(identity)["_metadata"]

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
                try:
                    book = openpyxl.load_workbook(path, data_only=True, read_only=True)
                except (OSError, ValueError, zipfile.BadZipFile, InvalidFileException) as exc:
                    raise HTTPException(
                        422,
                        "This spreadsheet cannot be read. Restore the source file and try again.",
                    ) from exc
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
            with open_pdf(path) as pdf:
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
            with open_pdf(path) as pdf:
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
