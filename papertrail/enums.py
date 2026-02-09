"""Dynamic enum loading and utilities."""

import json
from enum import Enum
from pathlib import Path
from typing import Optional

from papertrail.config import get_current_profile


_DOCUMENT_TYPES_LIST: list[str] | None = None


def reset_enum_cache() -> None:
    """Reset the enum cache, forcing re-evaluation on next access."""
    global _DOCUMENT_TYPES_LIST
    _DOCUMENT_TYPES_LIST = None


def clean_enum_string(value: str, enum_prefix: Optional[str] = None) -> str:
    """Remove enum prefix from serialized strings. E.g. 'DocumentType.invoice' -> 'invoice'."""
    if not isinstance(value, str):
        return value
    if enum_prefix:
        prefix = f"{enum_prefix}."
        if value.startswith(prefix):
            return value.split(".", 1)[-1]
    elif "." in value and value.count(".") == 1:
        return value.split(".", 1)[-1]
    return value


def create_dynamic_enum(name: str, values: list[str]) -> Enum:
    """Create a dynamic Enum class from a list of values."""
    return Enum(name, {k: k for k in values}, type=str)


FALLBACK_DOCUMENT_TYPES = [
    "$UNKNOWN$", "bank-note", "bank-statement", "contract", "contract-signup",
    "finance-balance", "finance-income", "insurance-auto", "insurance-notice",
    "invoice", "invoice-credit", "invoice-receipt", "notice", "notice-request",
    "other", "payroll-salary", "payroll-social", "payroll-vacation", "receipt",
    "tax-declaration", "tax-irs", "tax-vat"
]


def load_document_types(processed_files_dir: Optional[str] = None) -> list[str]:
    """Load document types from profile predefined, processed files, or fallback."""
    profile = get_current_profile()

    if profile and profile.document_types.predefined is not None:
        values = list(profile.document_types.predefined)
        if "$UNKNOWN$" not in values:
            values.append("$UNKNOWN$")
        return sorted(values)

    if processed_files_dir is None and profile and profile.paths.processed:
        processed_files_dir = profile.paths.processed

    values_set = set(FALLBACK_DOCUMENT_TYPES)
    if processed_files_dir and Path(processed_files_dir).exists():
        for json_file in Path(processed_files_dir).rglob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    value = json.load(f).get("document_type")
                if value and isinstance(value, str):
                    values_set.add(clean_enum_string(value, "DocumentType"))
            except Exception:
                continue

    values_set.add("$UNKNOWN$")
    return sorted(values_set)


def get_document_types() -> list[str]:
    """Get document types list (cached after first call)."""
    global _DOCUMENT_TYPES_LIST
    if _DOCUMENT_TYPES_LIST is None:
        _DOCUMENT_TYPES_LIST = load_document_types()
    return _DOCUMENT_TYPES_LIST


