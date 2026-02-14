"""Dynamic enum loading and utilities."""

import json
from pathlib import Path
from typing import Optional

from papertrail.config import get_current_profile


_DOCUMENT_TYPES_LIST: list[str] | None = None
_ISSUING_PARTIES_LIST: list[str] | None = None

# Session-scoped sets for values confirmed during a single run.
# Cleared automatically on next run (module-level state).
_session_types: set[str] = set()
_session_parties: set[str] = set()


def reset_enum_cache() -> None:
    """Reset the enum cache, forcing re-evaluation on next access."""
    global _DOCUMENT_TYPES_LIST, _ISSUING_PARTIES_LIST
    _DOCUMENT_TYPES_LIST = None
    _ISSUING_PARTIES_LIST = None


def reset_session_cache() -> None:
    """Reset session-scoped confirmed values."""
    _session_types.clear()
    _session_parties.clear()


def add_session_type(value: str) -> None:
    """Track a confirmed document type within the current run."""
    _session_types.add(value)


def add_session_party(value: str) -> None:
    """Track a confirmed issuing party within the current run."""
    _session_parties.add(value)


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


def _scan_json_field(processed_dir: str, field: str) -> set[str]:
    """Scan processed JSON files and collect unique values for a field."""
    values = set()
    path = Path(processed_dir)
    if not path.exists():
        return values
    for json_file in path.rglob("*.json"):
        if json_file.name.endswith(".reconciliation.json"):
            continue
        if any(part.startswith("_dupes") for part in json_file.parts):
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                value = json.load(f).get(field)
            if value and isinstance(value, str) and value != "$UNKNOWN$":
                values.add(clean_enum_string(value, "DocumentType" if field == "document_type" else None))
        except Exception:
            continue
    return values


def load_document_types(processed_files_dir: Optional[str] = None) -> list[str]:
    """Load document types from processed files + session confirmations."""
    profile = get_current_profile()

    if processed_files_dir is None and profile and profile.paths.processed:
        processed_files_dir = profile.paths.processed

    values_set: set[str] = set()
    if processed_files_dir:
        values_set = _scan_json_field(processed_files_dir, "document_type")

    values_set |= _session_types
    values_set.add("$UNKNOWN$")
    return sorted(values_set)


def load_issuing_parties(processed_files_dir: Optional[str] = None) -> list[str]:
    """Load issuing parties from processed files + session confirmations."""
    profile = get_current_profile()

    if processed_files_dir is None and profile and profile.paths.processed:
        processed_files_dir = profile.paths.processed

    values_set: set[str] = set()
    if processed_files_dir:
        values_set = _scan_json_field(processed_files_dir, "issuing_party")

    values_set |= _session_parties
    values_set.add("$UNKNOWN$")
    return sorted(values_set)


def _get_cached(cached: list[str] | None, loader, session_set: set[str]) -> tuple[list[str], list[str]]:
    """Return (result, new_cache) with session-aware cache invalidation."""
    if cached is None or session_set - set(cached):
        cached = loader()
    return cached, cached


def get_document_types() -> list[str]:
    """Get document types list (cached after first call, includes session types)."""
    global _DOCUMENT_TYPES_LIST
    _DOCUMENT_TYPES_LIST, result = _get_cached(_DOCUMENT_TYPES_LIST, load_document_types, _session_types)
    return result


def get_issuing_parties() -> list[str]:
    """Get issuing parties list (cached after first call, includes session parties)."""
    global _ISSUING_PARTIES_LIST
    _ISSUING_PARTIES_LIST, result = _get_cached(_ISSUING_PARTIES_LIST, load_issuing_parties, _session_parties)
    return result
