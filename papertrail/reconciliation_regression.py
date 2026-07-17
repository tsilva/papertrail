"""Golden regression checks for reconciliation approvals."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from papertrail.commands.reconcile import (
    MatchResult,
    _reconciliation_search_paths,
    reconcile_single,
)
from papertrail.reconciliation_groundtruth import (
    GROUNDTRUTH_SUFFIX,
    RECONCILIATION_SUFFIX,
    approval_map,
    document_sets_match,
    groundtruth_path_for_document,
    load_groundtruth,
    rows_with_transaction_keys,
    upsert_approval,
)
from papertrail.repository import DocumentRepository
from papertrail.runtime import Runtime


@dataclass
class RegressionResult:
    ok: bool
    seeded: int = 0
    checked: int = 0
    failures: list[str] = field(default_factory=list)


def seed_missing_approvals(runtime: Runtime, repository: DocumentRepository, export_path: Path) -> int:
    """Fill missing approvals from existing successful reconciliation sidecars."""
    file_index = _build_file_index(repository, export_path)
    seeded = 0
    for reconciliation_path in sorted(export_path.glob(f"*{RECONCILIATION_SUFFIX}")):
        statement_path = _statement_path_for_reconciliation(reconciliation_path)
        if not statement_path.exists():
            continue
        try:
            reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue

        groundtruth_path = groundtruth_path_for_document(statement_path)
        existing = approval_map(load_groundtruth(groundtruth_path))
        for row in rows_with_transaction_keys(reconciliation):
            if row.get("errors") or not row.get("files"):
                continue
            key_id = row["_transaction_key_id"]
            if key_id in existing:
                continue
            documents = [_document_identity(filename, file_index) for filename in row.get("files", [])]
            documents = [document for document in documents if document.get("filename")]
            if not documents:
                continue
            upsert_approval(
                groundtruth_path,
                source=statement_path.name,
                row=row,
                documents=documents,
            )
            existing[key_id] = {"required_documents": documents}
            seeded += 1

    if seeded:
        runtime.console.success(f"Seeded {seeded} reconciliation approval(s)", indent=False)
    return seeded


def verify_reconciliation_regression(
    runtime: Runtime,
    repository: DocumentRepository,
    export_path: Path,
    *,
    seed_missing: bool = False,
) -> RegressionResult:
    """Rerun reconciliation and compare current matches against approved groundtruth."""
    seeded = seed_missing_approvals(runtime, repository, export_path) if seed_missing else 0
    result = RegressionResult(ok=True, seeded=seeded)

    statements = []
    for json_path, data in repository.iter_sidecars(export_path):
        if data.get("document_type") != "bank-statement":
            continue
        doc_path = repository.find_companion(json_path, data)
        if doc_path is not None and doc_path.suffix.lower() == ".xlsx":
            statements.append(doc_path)
    statements = sorted(statements)
    if not statements:
        result.failures.append(f"No bank statements found in {export_path}")
        result.ok = False
        return result

    for statement_path in statements:
        _verify_statement(runtime, repository, export_path, statement_path, result)

    result.ok = not result.failures
    return result


def _verify_statement(
    runtime: Runtime,
    repository: DocumentRepository,
    export_path: Path,
    statement_path: Path,
    result: RegressionResult,
) -> None:
    groundtruth_path = statement_path.with_suffix(GROUNDTRUTH_SUFFIX)
    groundtruth = load_groundtruth(groundtruth_path)
    approvals = approval_map(groundtruth)
    if not approvals:
        result.failures.append(f"{statement_path.name}: no approved transactions in groundtruth")
        return

    stats = reconcile_single(runtime, repository, export_path, statement_path, dry_run=True, quiet=True)
    if stats.get("unmatched"):
        result.failures.append(f"{statement_path.name}: {stats['unmatched']} unmatched transaction(s)")
    if stats.get("incomplete"):
        result.failures.append(f"{statement_path.name}: {stats['incomplete']} incomplete transaction(s)")

    current = _current_approval_map(repository, stats.get("matches", []))
    approved_keys = set(approvals)
    current_keys = set(current)

    for missing_key in sorted(approved_keys - current_keys):
        description = approvals[missing_key].get("transaction", {}).get("description", "")
        result.failures.append(f"{statement_path.name}: missing approved transaction {description!r}")

    for extra_key in sorted(current_keys - approved_keys):
        description = current[extra_key].get("transaction", {}).get("description", "")
        result.failures.append(f"{statement_path.name}: unapproved current transaction {description!r}")

    for key_id in sorted(approved_keys & current_keys):
        approved_documents = approvals[key_id].get("required_documents", [])
        current_documents = current[key_id].get("required_documents", [])
        if not document_sets_match(current_documents, approved_documents, strict_hash=True):
            description = approvals[key_id].get("transaction", {}).get("description", "")
            result.failures.append(f"{statement_path.name}: document mismatch for {description!r}")

    result.checked += len(approved_keys)


def _current_approval_map(repository: DocumentRepository, matches: list[MatchResult]) -> dict[str, dict]:
    match_by_row = {match.transaction.row_number: match for match in matches}
    rows = [
        {
            "row": match.transaction.row_number,
            "date": match.transaction.date_posting or match.transaction.date_value,
            "description": match.transaction.description,
            "amount": match.transaction.amount,
            "currency": match.transaction.currency,
            "files": [candidate.pdf_filename for candidate in match.pdf_candidates],
        }
        for match in matches
    ]
    current: dict[str, dict] = {}
    for row in rows_with_transaction_keys({"matches": rows, "unmatched": []}):
        match = match_by_row[row["row"]]
        documents = [_candidate_identity(repository, candidate) for candidate in match.pdf_candidates]
        current[row["_transaction_key_id"]] = {
            "transaction": row["_transaction_key"],
            "required_documents": documents,
        }
    return current


def _candidate_identity(repository: DocumentRepository, candidate) -> dict:
    try:
        metadata = repository.load_metadata(candidate.json_path)
    except Exception:
        metadata = {}
    return {
        "filename": candidate.pdf_filename,
        "hash_file": metadata.get("hash_file") or candidate.hash_file,
        "hash_content": metadata.get("hash_content"),
    }


def _build_file_index(repository: DocumentRepository, export_path: Path) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for search_path in _reconciliation_search_paths(export_path):
        for json_path, data in repository.iter_sidecars(search_path):
            doc_path = repository.find_companion(json_path, data)
            if doc_path is None:
                continue
            index.setdefault(
                doc_path.name,
                {
                    "filename": doc_path.name,
                    "hash_file": data.get("hash_file"),
                    "hash_content": data.get("hash_content"),
                },
            )
    return index


def _document_identity(filename: str, file_index: dict[str, dict]) -> dict:
    return dict(file_index.get(filename) or {"filename": filename, "hash_file": None, "hash_content": None})


def _statement_path_for_reconciliation(reconciliation_path: Path) -> Path:
    name = reconciliation_path.name
    if name.endswith(RECONCILIATION_SUFFIX):
        return reconciliation_path.with_name(f"{name[:-len(RECONCILIATION_SUFFIX)]}.xlsx")
    return reconciliation_path.with_suffix(".xlsx")
