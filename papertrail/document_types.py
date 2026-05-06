"""Document type normalization helpers."""

from __future__ import annotations

import re

from papertrail.models import clean_enum_string
from papertrail.utils import strip_diacritics

_BANK_NOTE_RAW_TYPES = {"movimento", "notadelancamento"}
_LOAN_SIMULATION_RAW_TYPES = {"simulacao", "simulation"}
_LOAN_SIMULATION_CONTEXT_TERMS = {"credito", "credit", "loan", "emprestimo", "financiamento"}


def normalize_document_type(
    value: str | None,
    raw_value: str | None = None,
    document_title: str | None = None,
) -> str | None:
    """Apply deterministic document-type overrides before registry canonicalization."""
    if is_bank_note_raw_type(raw_value):
        return "bank-note"
    if is_loan_simulation(raw_value, document_title):
        return "loan-simulation"
    if value is None:
        return None
    return clean_enum_string(value, "DocumentType")


def is_bank_note_raw_type(raw_value: str | None) -> bool:
    if raw_value is None:
        return False
    return _compact_token(raw_value) in _BANK_NOTE_RAW_TYPES


def is_loan_simulation(raw_value: str | None, document_title: str | None = None) -> bool:
    if raw_value is None:
        return False
    if _compact_token(raw_value) not in _LOAN_SIMULATION_RAW_TYPES:
        return False
    context = _word_tokens(f"{raw_value or ''} {document_title or ''}")
    return any(term in context for term in _LOAN_SIMULATION_CONTEXT_TERMS)


def _compact_token(value: str) -> str:
    cleaned = clean_enum_string(value).strip()
    if not cleaned:
        return ""
    return re.sub(r"[^a-z0-9]+", "", strip_diacritics(cleaned).lower())


def _word_tokens(value: str) -> set[str]:
    cleaned = strip_diacritics(clean_enum_string(value).lower())
    return set(re.findall(r"[a-z0-9]+", cleaned))
