"""Document type normalization helpers."""

from __future__ import annotations

import re

from papertrail.models import clean_enum_string
from papertrail.utils import strip_diacritics

_BANK_NOTE_RAW_TYPES = {"movimento", "notadelancamento"}


def normalize_document_type(value: str | None, raw_value: str | None = None) -> str | None:
    """Apply deterministic document-type overrides before registry canonicalization."""
    if is_bank_note_raw_type(raw_value):
        return "bank-note"
    if value is None:
        return None
    return clean_enum_string(value, "DocumentType")


def is_bank_note_raw_type(raw_value: str | None) -> bool:
    if raw_value is None:
        return False
    cleaned = clean_enum_string(raw_value).strip()
    if not cleaned:
        return False
    token = re.sub(r"[^a-z0-9]+", "", strip_diacritics(cleaned).lower())
    return token in _BANK_NOTE_RAW_TYPES
