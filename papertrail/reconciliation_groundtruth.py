"""Durable human approvals for reconciliation results."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


GROUNDTRUTH_SUFFIX = ".reconciliation.groundtruth.json"
RECONCILIATION_SUFFIX = ".reconciliation.json"


def is_reconciliation_sidecar(path_or_name: str | Path) -> bool:
    name = Path(path_or_name).name
    return name.endswith(RECONCILIATION_SUFFIX) or name.endswith(GROUNDTRUTH_SUFFIX)


def groundtruth_path_for_document(document_path: Path) -> Path:
    return document_path.with_suffix(GROUNDTRUTH_SUFFIX)


def normalize_description(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text.casefold()).strip()
    return text


def normalize_amount(value: Any) -> str:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        amount = Decimal("0.00")
    if amount == Decimal("-0.00"):
        amount = Decimal("0.00")
    return f"{amount:.2f}"


def transaction_base_key(row: dict) -> dict:
    return {
        "date": row.get("date") or "",
        "description_normalized": normalize_description(row.get("description", "")),
        "amount": normalize_amount(row.get("amount", 0)),
        "currency": str(row.get("currency") or "").upper(),
    }


def transaction_key(row: dict, occurrence: int) -> dict:
    key = transaction_base_key(row)
    key["occurrence"] = occurrence
    return key


def transaction_key_id(key: dict) -> str:
    return json.dumps(
        {
            "date": key.get("date") or "",
            "description_normalized": key.get("description_normalized") or "",
            "amount": normalize_amount(key.get("amount", 0)),
            "currency": str(key.get("currency") or "").upper(),
            "occurrence": int(key.get("occurrence") or 1),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def rows_with_transaction_keys(reconciliation: dict) -> list[dict]:
    rows = []
    for match in reconciliation.get("matches", []):
        rows.append(dict(match))
    for unmatched in reconciliation.get("unmatched", []):
        row = dict(unmatched)
        row.setdefault("files", [])
        row.setdefault("confidence", 0)
        row.setdefault("method", "")
        row.setdefault("reasoning", "")
        row.setdefault("warnings", [])
        rows.append(row)

    rows.sort(key=lambda row: row.get("row", 0))

    occurrence_counts: dict[str, int] = {}
    keyed_rows = []
    for row in rows:
        base_id = transaction_key_id({**transaction_base_key(row), "occurrence": 1})
        occurrence = occurrence_counts.get(base_id, 0) + 1
        occurrence_counts[base_id] = occurrence
        keyed = dict(row)
        keyed["_transaction_key"] = transaction_key(row, occurrence)
        keyed["_transaction_key_id"] = transaction_key_id(keyed["_transaction_key"])
        keyed_rows.append(keyed)

    return keyed_rows


def load_groundtruth(path: Path) -> dict:
    if not path.exists():
        return {
            "schema_version": 1,
            "source": "",
            "approvals": [],
            "unmatched_file_approvals": [],
        }
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("schema_version", 1)
    data.setdefault("source", "")
    data.setdefault("approvals", [])
    data.setdefault("unmatched_file_approvals", [])
    return data


def save_groundtruth(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def approval_map(groundtruth: dict | None) -> dict[str, dict]:
    if not groundtruth:
        return {}
    approvals = {}
    for approval in groundtruth.get("approvals", []):
        key = approval.get("transaction", {})
        if key:
            approvals[transaction_key_id(key)] = approval
    return approvals


def unmatched_file_approvals(groundtruth: dict | None) -> list[dict]:
    if not groundtruth:
        return []
    return list(groundtruth.get("unmatched_file_approvals", []))


def _unmatched_file_sort_key(item: dict) -> tuple[str, str, str]:
    document = item.get("document", {})
    return (
        document.get("filename", ""),
        document.get("hash_file", "") or "",
        document.get("hash_content", "") or "",
    )


def upsert_approval(
    groundtruth_path: Path,
    *,
    source: str,
    row: dict,
    documents: list[dict],
) -> dict:
    data = load_groundtruth(groundtruth_path)
    data["schema_version"] = 1
    data["source"] = source
    data["updated"] = datetime.now().isoformat(timespec="seconds")

    key = row["_transaction_key"]
    approval = {
        "confirmed_at": datetime.now().isoformat(timespec="seconds"),
        "transaction": {
            **key,
            "description": row.get("description", ""),
        },
        "required_documents": documents,
        "source_hint": {
            "statement_file": source,
            "row": row.get("row"),
        },
    }

    approvals = approval_map(data)
    approvals[transaction_key_id(key)] = approval
    data["approvals"] = sorted(
        approvals.values(),
        key=lambda item: (
            item.get("transaction", {}).get("date", ""),
            item.get("transaction", {}).get("amount", ""),
            item.get("transaction", {}).get("description_normalized", ""),
            item.get("transaction", {}).get("occurrence", 1),
        ),
    )
    save_groundtruth(groundtruth_path, data)
    return approval


def remove_approval(groundtruth_path: Path, *, row: dict) -> bool:
    if not groundtruth_path.exists():
        return False

    data = load_groundtruth(groundtruth_path)
    key_id = row.get("_transaction_key_id")
    before = len(data.get("approvals", []))
    data["approvals"] = [
        approval
        for approval in data.get("approvals", [])
        if transaction_key_id(approval.get("transaction", {})) != key_id
    ]
    changed = len(data["approvals"]) != before
    if changed:
        data["updated"] = datetime.now().isoformat(timespec="seconds")
        save_groundtruth(groundtruth_path, data)
    return changed


def upsert_unmatched_file_approval(
    groundtruth_path: Path,
    *,
    source: str,
    document: dict,
) -> dict:
    data = load_groundtruth(groundtruth_path)
    data["schema_version"] = 1
    data["source"] = source
    data["updated"] = datetime.now().isoformat(timespec="seconds")

    approval = {
        "confirmed_at": datetime.now().isoformat(timespec="seconds"),
        "document": document,
        "status": "expected_unreconciled",
        "source_hint": {
            "statement_file": source,
        },
    }

    approvals = [
        item
        for item in data.get("unmatched_file_approvals", [])
        if not document_matches_approval(document, item.get("document", {}))
    ]
    approvals.append(approval)
    data["unmatched_file_approvals"] = sorted(
        approvals,
        key=_unmatched_file_sort_key,
    )
    save_groundtruth(groundtruth_path, data)
    return approval


def remove_unmatched_file_approval(groundtruth_path: Path, *, document: dict) -> bool:
    if not groundtruth_path.exists():
        return False

    data = load_groundtruth(groundtruth_path)
    before = len(data.get("unmatched_file_approvals", []))
    data["unmatched_file_approvals"] = [
        approval
        for approval in data.get("unmatched_file_approvals", [])
        if not document_matches_approval(document, approval.get("document", {}))
    ]
    changed = len(data["unmatched_file_approvals"]) != before
    if changed:
        data["updated"] = datetime.now().isoformat(timespec="seconds")
        save_groundtruth(groundtruth_path, data)
    return changed


def document_matches_approval(current: dict, approved: dict) -> bool:
    if approved.get("hash_file") and current.get("hash_file") == approved.get("hash_file"):
        return True
    if approved.get("hash_content") and current.get("hash_content") == approved.get("hash_content"):
        return True
    return bool(approved.get("filename") and current.get("filename") == approved.get("filename"))


def document_sets_match(current_documents: list[dict], approved_documents: list[dict]) -> bool:
    if len(current_documents) != len(approved_documents):
        return False

    remaining = list(current_documents)
    for approved in approved_documents:
        match_index = next(
            (
                index
                for index, current in enumerate(remaining)
                if document_matches_approval(current, approved)
            ),
            None,
        )
        if match_index is None:
            return False
        remaining.pop(match_index)
    return True
