"""Document type normalization helpers."""

from __future__ import annotations

import re

from papertrail.models import clean_enum_string
from papertrail.utils import strip_diacritics

DEFAULT_DOCUMENT_TYPE_OVERRIDES = (
    {"target": "bank-note", "raw_types": ("movimento", "notadelancamento")},
    {
        "target": "investment-acquisition-summary",
        "context_all": ("mapa", "resumo", "datas", "valores", "aquisicao", "mobiliarios"),
    },
    {
        "target": "loan-simulation",
        "raw_types": ("simulacao", "simulation"),
        "context_any": ("credito", "credit", "loan", "emprestimo", "financiamento"),
    },
    {"target": "bank-note", "context_all": ("concess", "cred", "empr")},
)


def normalize_document_type(
    value: str | None,
    raw_value: str | None = None,
    document_title: str | None = None,
    overrides: list | tuple | None = None,
) -> str | None:
    """Apply deterministic document-type overrides before registry canonicalization."""
    override = match_document_type_override(raw_value, document_title, overrides)
    if override:
        return override
    if value is None:
        return None
    return clean_enum_string(value, "DocumentType")


def match_document_type_override(
    raw_value: str | None,
    document_title: str | None = None,
    overrides: list | tuple | None = None,
) -> str | None:
    raw_token = _compact_token(raw_value or "")
    context = _word_tokens(f"{raw_value or ''} {document_title or ''}")
    for override in overrides or DEFAULT_DOCUMENT_TYPE_OVERRIDES:
        target = _override_get(override, "target")
        if not target:
            continue

        raw_types = {_compact_token(str(item)) for item in _override_get(override, "raw_types", [])}
        if raw_types and raw_token not in raw_types:
            continue

        context_all = set(_override_get(override, "context_all", []))
        if context_all and not context_all.issubset(context):
            continue

        context_any = set(_override_get(override, "context_any", []))
        if context_any and not any(term in context for term in context_any):
            continue

        return str(target)
    return None


def _override_get(override: object, key: str, default=None):
    if isinstance(override, dict):
        return override.get(key, default)
    return getattr(override, key, default)


def _compact_token(value: str) -> str:
    cleaned = clean_enum_string(value).strip()
    if not cleaned:
        return ""
    return re.sub(r"[^a-z0-9]+", "", strip_diacritics(cleaned).lower())


def _word_tokens(value: str) -> set[str]:
    cleaned = strip_diacritics(clean_enum_string(value).lower())
    return set(re.findall(r"[a-z0-9]+", cleaned))
