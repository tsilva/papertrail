#!/usr/bin/env python3
"""Read-only audit for a Papertrail monthly extraction and reconciliation run."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from papertrail.config import ConfigError, load_profile
from papertrail.repository import document_sidecar_paths, find_companion

UNKNOWN = "$UNKNOWN$"
REQUIRED_FIELDS = (
    "hash_content",
    "hash_file",
    "date_issued",
    "document_type",
    "issuing_party",
    "class_confidence",
)
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def previous_month(today: date | None = None) -> str:
    current = today or date.today()
    prior = current.replace(day=1) - timedelta(days=1)
    return prior.strftime("%Y-%m")


def add_smell(
    smells: list[dict[str, Any]],
    code: str,
    severity: str,
    detail: str,
    path: Path | None = None,
) -> None:
    item: dict[str, Any] = {"code": code, "severity": severity, "detail": detail}
    if path is not None:
        item["path"] = str(path)
    smells.append(item)


def load_sidecars(root: Path, smells: list[dict[str, Any]]) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    if not root.is_dir():
        add_smell(smells, "missing-directory", "error", "Configured directory does not exist", root)
        return records

    for path in document_sidecar_paths(root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            add_smell(smells, "invalid-sidecar", "error", str(exc), path)
            continue
        if not isinstance(payload, dict):
            add_smell(smells, "invalid-sidecar", "error", "Sidecar root is not an object", path)
            continue
        records.append((path, payload))
    return records


def valid_iso_date(value: object) -> bool:
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def audit_metadata_record(
    sidecar: Path,
    data: dict[str, Any],
    *,
    scope: str,
    month: str,
    confidence_threshold: float,
    smells: list[dict[str, Any]],
) -> None:
    companion = find_companion(sidecar, data)
    if companion is None:
        add_smell(smells, "missing-companion", "error", f"{scope} sidecar has no document", sidecar)
    elif UNKNOWN in companion.name:
        add_smell(
            smells, "unknown-filename", "error", "Companion filename contains $UNKNOWN$", companion
        )
    if UNKNOWN in sidecar.name:
        add_smell(
            smells, "unknown-filename", "error", "Sidecar filename contains $UNKNOWN$", sidecar
        )

    for field in REQUIRED_FIELDS:
        if data.get(field) in (None, ""):
            add_smell(smells, "missing-critical-field", "error", f"Missing {field}", sidecar)

    if data.get("document_title") in (None, ""):
        add_smell(smells, "missing-document-title", "warning", "Missing document_title", sidecar)
    if companion and companion.suffix.lower() == ".pdf" and data.get("page_count") in (None, ""):
        add_smell(smells, "missing-page-count", "warning", "PDF has no page_count", sidecar)

    for field in ("document_type", "issuing_party", "date_issued"):
        if data.get(field) == UNKNOWN:
            add_smell(smells, "unknown-metadata", "error", f"{field} is $UNKNOWN$", sidecar)

    issued = data.get("date_issued")
    if issued not in (None, "", UNKNOWN):
        if not valid_iso_date(issued):
            add_smell(smells, "invalid-date", "error", f"Invalid date_issued: {issued!r}", sidecar)
        elif scope == "export" and not str(issued).startswith(f"{month}-"):
            add_smell(
                smells,
                "out-of-month-export",
                "warning",
                f"Exported date {issued} is outside {month}; verify whether approval "
                "restoration requires it",
                sidecar,
            )

    confidence = data.get("class_confidence")
    if isinstance(confidence, (int, float)) and confidence < confidence_threshold:
        add_smell(
            smells,
            "low-classification-confidence",
            "warning",
            f"class_confidence {confidence:.3f} is below {confidence_threshold:.3f}",
            sidecar,
        )

    amount = data.get("total_amount")
    currency = data.get("total_amount_currency")
    if amount is not None and not currency:
        add_smell(
            smells,
            "amount-without-currency",
            "warning",
            f"Amount {amount!r} has no currency",
            sidecar,
        )
    if currency and amount is None and not data.get("bank_statement"):
        add_smell(
            smells,
            "currency-without-amount",
            "warning",
            f"Currency {currency!r} has no amount",
            sidecar,
        )

    for index, sub_document in enumerate(data.get("sub_documents") or []):
        if not isinstance(sub_document, dict):
            continue
        for field in ("document_type", "issuing_party", "date_issued"):
            if sub_document.get(field) == UNKNOWN:
                add_smell(
                    smells,
                    "unknown-sub-document-metadata",
                    "error",
                    f"sub_documents[{index}].{field} is $UNKNOWN$",
                    sidecar,
                )


def duplicate_hash_smells(
    records: list[tuple[Path, dict[str, Any]]],
    scope: str,
    smells: list[dict[str, Any]],
) -> None:
    by_hash: dict[str, list[Path]] = defaultdict(list)
    for path, data in records:
        value = data.get("hash_content")
        if value:
            by_hash[str(value)].append(path)
    for digest, paths in sorted(by_hash.items()):
        if len(paths) < 2:
            continue
        add_smell(
            smells,
            "duplicate-content-hash",
            "warning",
            f"{scope} contains {len(paths)} sidecars with hash_content {digest}: "
            + ", ".join(path.name for path in paths),
        )


def reconciliation_audit(
    month_dir: Path,
    export_records: list[tuple[Path, dict[str, Any]]],
    smells: list[dict[str, Any]],
) -> dict[str, Any]:
    statements: list[tuple[Path, Path, dict[str, Any]]] = []
    for sidecar, data in export_records:
        companion = find_companion(sidecar, data)
        if (
            data.get("document_type") == "bank-statement"
            and companion
            and companion.suffix.lower() == ".xlsx"
        ):
            statements.append((sidecar, companion, data))

    latest_metadata_mtime = max((path.stat().st_mtime for path, _ in export_records), default=0.0)
    statement_results: list[dict[str, Any]] = []
    matched_files: set[str] = set()
    unmatched_candidates: dict[str, dict[str, Any]] = {}

    for statement_sidecar, statement, metadata in sorted(statements, key=lambda item: item[1].name):
        result: dict[str, Any] = {
            "source": statement.name,
            "account": (metadata.get("bank_statement") or {}).get("account_number"),
            "sidecar": str(statement.with_suffix(".reconciliation.json")),
            "complete": False,
            "issues": [],
        }
        reconciliation_path = statement.with_suffix(".reconciliation.json")
        if not reconciliation_path.exists():
            result["issues"].append("missing reconciliation sidecar")
            statement_results.append(result)
            continue

        try:
            payload = json.loads(reconciliation_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            result["issues"].append(f"invalid reconciliation sidecar: {exc}")
            statement_results.append(result)
            continue

        summary = payload.get("summary") if isinstance(payload, dict) else None
        if not isinstance(summary, dict):
            result["issues"].append("missing reconciliation summary")
            statement_results.append(result)
            continue

        for key in (
            "total",
            "reconciled",
            "incomplete",
            "unmatched",
            "unmatched_files",
            "reconciliation_rate",
        ):
            result[key] = summary.get(key)

        total = summary.get("total")
        reconciled = summary.get("reconciled")
        incomplete = summary.get("incomplete")
        unmatched = summary.get("unmatched")
        rate = summary.get("reconciliation_rate")
        if not isinstance(total, int) or total <= 0:
            result["issues"].append("statement has no transactions")
        if not all(isinstance(value, int) for value in (reconciled, incomplete, unmatched)):
            result["issues"].append("summary counts are missing or non-integer")
        elif isinstance(total, int) and reconciled + incomplete + unmatched != total:
            result["issues"].append("summary counts do not add up to total")
        if isinstance(total, int) and isinstance(reconciled, int) and reconciled != total:
            result["issues"].append(f"only {reconciled}/{total} transactions reconciled")
        if incomplete != 0:
            result["issues"].append(f"{incomplete} incomplete transaction(s)")
        if unmatched != 0:
            result["issues"].append(f"{unmatched} unmatched transaction(s)")
        if rate != 100.0:
            result["issues"].append(f"reconciliation rate is {rate!r}, not 100.0")
        if payload.get("source") != statement.name:
            result["issues"].append("reconciliation source does not match statement filename")
        if reconciliation_path.stat().st_mtime < max(
            latest_metadata_mtime, statement_sidecar.stat().st_mtime
        ):
            result["issues"].append("reconciliation sidecar is older than export metadata")

        for match in payload.get("matches") or []:
            if not isinstance(match, dict):
                continue
            files = [str(value) for value in (match.get("files") or [])]
            matched_files.update(files)
            method = match.get("method")
            confidence = match.get("confidence")
            if method == "llm":
                add_smell(
                    smells,
                    "llm-reconciliation-match",
                    "warning",
                    f"Review statement {statement.name} row {match.get('row')} "
                    f"LLM match: {', '.join(files)}",
                    reconciliation_path,
                )
            if isinstance(confidence, (int, float)) and confidence < 1.0:
                add_smell(
                    smells,
                    "low-reconciliation-confidence",
                    "warning",
                    f"Statement {statement.name} row {match.get('row')} confidence is {confidence}",
                    reconciliation_path,
                )
            if match.get("errors"):
                result["issues"].append(f"row {match.get('row')} has match errors")

        for candidate in payload.get("unmatched_files") or []:
            if isinstance(candidate, dict) and candidate.get("file"):
                unmatched_candidates[str(candidate["file"])] = candidate

        result["complete"] = not result["issues"]
        statement_results.append(result)

    globally_unmatched = [
        unmatched_candidates[name]
        for name in sorted(unmatched_candidates)
        if name not in matched_files
    ]
    complete = bool(statement_results) and all(item["complete"] for item in statement_results)
    issues = [] if statement_results else [f"no XLSX bank statements found in {month_dir}"]
    return {
        "complete": complete,
        "issues": issues,
        "statements": statement_results,
        "globally_unmatched_candidates": globally_unmatched,
    }


def distributions(records: list[tuple[Path, dict[str, Any]]]) -> dict[str, dict[str, int]]:
    return {
        "document_types": dict(
            sorted(Counter(str(data.get("document_type")) for _, data in records).items())
        ),
        "issuing_parties": dict(
            sorted(Counter(str(data.get("issuing_party")) for _, data in records).items())
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Papertrail profile name")
    parser.add_argument("--month", default=previous_month(), help="Target month in YYYY-MM format")
    parser.add_argument(
        "--since-file", type=Path, help="Include processed sidecars newer than this marker"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        date.fromisoformat(f"{args.month}-01")
    except ValueError:
        print(json.dumps({"error": "--month must be YYYY-MM"}, indent=2))
        return 2

    try:
        profile = load_profile(args.profile)
    except ConfigError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2

    smells: list[dict[str, Any]] = []
    parsed_url = urlparse(profile.openrouter.base_url)
    agentbridge_configured = (
        parsed_url.scheme == "http"
        and parsed_url.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed_url.path.rstrip("/") == "/api/v1"
        and str(profile.openrouter.model_id or "").startswith("codex/")
    )
    if not agentbridge_configured:
        add_smell(
            smells,
            "non-agentbridge-configuration",
            "error",
            "Expected loopback /api/v1 with a codex/* model, got "
            f"{profile.openrouter.base_url} and {profile.openrouter.model_id}",
        )

    processed_root = (
        Path(profile.paths.processed) if profile.paths.processed else Path("__missing_processed__")
    )
    export_root = Path(profile.paths.export) if profile.paths.export else Path("__missing_export__")
    month_dir = export_root / args.month
    processed_records = load_sidecars(processed_root, smells)
    export_records = load_sidecars(month_dir, smells)

    since_mtime = None
    if args.since_file:
        if args.since_file.exists():
            since_mtime = args.since_file.stat().st_mtime
        else:
            add_smell(
                smells,
                "missing-since-marker",
                "warning",
                "Timestamp marker does not exist",
                args.since_file,
            )

    selected_processed = [
        (path, data)
        for path, data in processed_records
        if str(data.get("date_issued") or "").startswith(f"{args.month}-")
        or (since_mtime is not None and path.stat().st_mtime >= since_mtime)
    ]

    threshold = float(profile.tools.llm_high_confidence_threshold)
    for path, data in selected_processed:
        audit_metadata_record(
            path,
            data,
            scope="processed",
            month=args.month,
            confidence_threshold=threshold,
            smells=smells,
        )
    for path, data in export_records:
        audit_metadata_record(
            path,
            data,
            scope="export",
            month=args.month,
            confidence_threshold=threshold,
            smells=smells,
        )

    duplicate_hash_smells(selected_processed, "processed selection", smells)
    duplicate_hash_smells(export_records, "month export", smells)
    reconciliation = reconciliation_audit(month_dir, export_records, smells)

    report = {
        "profile": args.profile,
        "month": args.month,
        "status": "complete"
        if agentbridge_configured and reconciliation["complete"]
        else "incomplete",
        "agentbridge": {
            "configured": agentbridge_configured,
            "base_url": profile.openrouter.base_url,
            "model_id": profile.openrouter.model_id,
        },
        "paths": {
            "processed": str(processed_root),
            "export_month": str(month_dir),
        },
        "extraction": {
            "selected_processed_documents": len(selected_processed),
            "export_documents": len(export_records),
            "distributions": distributions(export_records),
        },
        "reconciliation": reconciliation,
        "smells": sorted(
            smells,
            key=lambda item: (
                {"error": 0, "warning": 1}.get(item["severity"], 2),
                item["code"],
                item.get("path", ""),
            ),
        ),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
