"""Dynamic enum loading and utilities."""

import json
from enum import Enum
from pathlib import Path
from typing import Optional

from papertrail.config import get_current_profile


# ============================================================================
# Lazy Loading Cache
# ============================================================================

_DOCUMENT_TYPES_LIST: list[str] | None = None


def reset_enum_cache() -> None:
    """Reset the enum cache, forcing re-evaluation on next access."""
    global _DOCUMENT_TYPES_LIST
    _DOCUMENT_TYPES_LIST = None


# ============================================================================
# Utilities
# ============================================================================


def clean_enum_string(value: str, enum_prefix: Optional[str] = None) -> str:
    """
    Remove enum prefix from serialized enum strings.

    Handles formats like "DocumentType.invoice" -> "invoice"
    """
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
    return Enum(name, dict([(k, k) for k in values]), type=str)


# ============================================================================
# Hardcoded Fallbacks
# ============================================================================

FALLBACK_DOCUMENT_TYPES = [
    "$UNKNOWN$", "bank-note", "bank-statement", "contract", "contract-signup",
    "finance-balance", "finance-income", "insurance-auto", "insurance-notice",
    "invoice", "invoice-credit", "invoice-receipt", "notice", "notice-request",
    "other", "payroll-salary", "payroll-social", "payroll-vacation", "receipt",
    "tax-declaration", "tax-irs", "tax-vat"
]


# ============================================================================
# Main Loaders
# ============================================================================


def _load_enum_values(
    processed_files_dir: Optional[str],
    profile_predefined_attr: str,
    fallback_list: list[str],
    json_field: str,
    enum_prefix: str,
) -> list[str]:
    """Shared loader for enum values (e.g. document types).

    Args:
        processed_files_dir: Path to processed files directory.
        profile_predefined_attr: Attribute name on profile (e.g. 'document_types').
        fallback_list: Hardcoded fallback values.
        json_field: Key to read from metadata JSON files.
        enum_prefix: Prefix for clean_enum_string (e.g. 'DocumentType').

    Returns:
        Sorted list of enum values.
    """
    profile = get_current_profile()

    # Check profile for predefined values
    if profile:
        predefined = getattr(profile, profile_predefined_attr, None)
        if predefined is not None and predefined.predefined is not None:
            values = list(predefined.predefined)
            if "$UNKNOWN$" not in values:
                values.append("$UNKNOWN$")
            return sorted(values)

    # Determine processed_files_dir
    if processed_files_dir is None:
        if profile and profile.paths.processed:
            processed_files_dir = profile.paths.processed

    # Start with fallback values
    values_set = set(fallback_list)

    # Scan processed files for dynamic values if path exists
    if processed_files_dir and Path(processed_files_dir).exists():
        processed_path = Path(processed_files_dir)

        for json_file in processed_path.rglob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    value = data.get(json_field)
                    if value and isinstance(value, str):
                        value = clean_enum_string(value, enum_prefix)
                        values_set.add(value)
            except Exception:
                continue

    values_set.add("$UNKNOWN$")
    return sorted(values_set)


def load_document_types(processed_files_dir: Optional[str] = None) -> list[str]:
    """Load document types from profile predefined, processed files, or fallback."""
    return _load_enum_values(
        processed_files_dir, "document_types", FALLBACK_DOCUMENT_TYPES,
        "document_type", "DocumentType",
    )


# Convenience functions with caching for fast repeated access
def get_document_types() -> list[str]:
    """Get document types list (cached after first call)."""
    global _DOCUMENT_TYPES_LIST
    if _DOCUMENT_TYPES_LIST is None:
        _DOCUMENT_TYPES_LIST = load_document_types()
    return _DOCUMENT_TYPES_LIST


