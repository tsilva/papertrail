"""Derived reconciliation evidence fields.

These helpers intentionally do not rewrite historical sidecars. They compute a
smaller reconciliation view from the existing metadata surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from papertrail.models import clean_enum_string
from papertrail.reconciliation_defaults import (
    DEFAULT_BANK_COUNTERPARTIES,
    DEFAULT_COUNTERPARTY_ALIASES,
    DEFAULT_DOCUMENT_FAMILIES,
    DEFAULT_SHARED_PERIOD_TITLE_TERMS,
    DEFAULT_TAX_NUMBER_DEFAULT_COUNTRY_PREFIX,
)
from papertrail.utils import compact_match_key, strip_diacritics

BANK_ANCHOR = "bank_anchor"
SUPPLIER_EVIDENCE = "supplier_evidence"
TAX_EVIDENCE = "tax_evidence"
PAYROLL_EVIDENCE = "payroll_evidence"
LOAN_EVIDENCE = "loan_evidence"
INVESTMENT_EVIDENCE = "investment_evidence"
CONTRACT_EVIDENCE = "contract_evidence"
IGNORE = "ignore"
UNKNOWN = "unknown"

_BANK_COUNTERPARTIES = set(DEFAULT_BANK_COUNTERPARTIES)
_COUNTERPARTY_ALIASES = dict(DEFAULT_COUNTERPARTY_ALIASES)
_SHARED_PERIOD_TITLE_TERMS = {
    party: tuple(terms)
    for party, terms in DEFAULT_SHARED_PERIOD_TITLE_TERMS.items()
}
_DOCUMENT_FAMILIES = {
    str(family): dict(settings)
    for family, settings in DEFAULT_DOCUMENT_FAMILIES.items()
    if isinstance(settings, dict)
}


@dataclass(frozen=True)
class DocumentEvidence:
    document_family: str
    counterparty_id: str
    source_bank: str | None = None
    is_shared_period_document: bool = False

    @property
    def is_bank_anchor(self) -> bool:
        return self.document_family == BANK_ANCHOR

    @property
    def is_supplier_evidence(self) -> bool:
        return self.document_family == SUPPLIER_EVIDENCE

    @property
    def is_ignored_for_reconciliation(self) -> bool:
        return self.document_family == IGNORE


def document_family_for_type(
    doc_type: str | None,
    metadata: dict[str, Any] | None = None,
    *,
    document_families: dict[str, dict[str, Any]] | None = None,
) -> str:
    doc_type = clean_enum_string(doc_type or "", "DocumentType").strip().lower()
    metadata = metadata or {}
    families = _document_families(document_families)

    if _matches_family(doc_type, families.get(IGNORE, {})):
        return IGNORE
    supplier_config = families.get(SUPPLIER_EVIDENCE, {})
    if _is_zero_amount_supplier_doc(doc_type, metadata.get("total_amount"), supplier_config):
        return IGNORE

    for family, settings in families.items():
        if family == IGNORE:
            continue
        if _matches_family(doc_type, settings):
            return family
    if not doc_type:
        return UNKNOWN
    return UNKNOWN


def document_type_matches_family(
    doc_type: str | None,
    family_pattern: str,
    *,
    document_families: dict[str, dict[str, Any]] | None = None,
) -> bool:
    families = _family_aliases(document_families).get(family_pattern.strip().lower())
    if not families:
        return False
    return document_family_for_type(doc_type, document_families=document_families) in families


def counterparty_id(
    metadata: dict[str, Any],
    *,
    counterparty_aliases: dict[str, str] | None = None,
    tax_number_default_country_prefix: str | None = DEFAULT_TAX_NUMBER_DEFAULT_COUNTRY_PREFIX,
) -> str:
    candidates = [
        metadata.get("issuer_tax_number"),
        metadata.get("issuing_party"),
        metadata.get("issuing_party_raw"),
    ]
    for value in candidates:
        alias = _alias_for_value(value, counterparty_aliases)
        if alias:
            return alias

    tax_number = _normalize_tax_number(
        metadata.get("issuer_tax_number"),
        default_country_prefix=tax_number_default_country_prefix,
    )
    if tax_number:
        return f"tax:{tax_number}"

    for value in (metadata.get("issuing_party"), metadata.get("issuing_party_raw")):
        slug = _slug(value)
        if slug:
            return slug
    return "$UNKNOWN$"


def build_document_evidence(
    metadata: dict[str, Any],
    *,
    counterparty_aliases: dict[str, str] | None = None,
    bank_counterparties: list[str] | tuple[str, ...] | set[str] | None = None,
    shared_period_title_terms: dict[str, list[str]] | dict[str, tuple[str, ...]] | None = None,
    document_families: dict[str, dict[str, Any]] | None = None,
    tax_number_default_country_prefix: str | None = DEFAULT_TAX_NUMBER_DEFAULT_COUNTRY_PREFIX,
) -> DocumentEvidence:
    family = document_family_for_type(
        metadata.get("document_type"),
        metadata,
        document_families=document_families,
    )
    party = counterparty_id(
        metadata,
        counterparty_aliases=counterparty_aliases,
        tax_number_default_country_prefix=tax_number_default_country_prefix,
    )
    bank_parties = set(bank_counterparties or _BANK_COUNTERPARTIES)
    source_bank = party if family == BANK_ANCHOR and party in bank_parties else None
    return DocumentEvidence(
        document_family=family,
        counterparty_id=party,
        source_bank=source_bank,
        is_shared_period_document=_is_shared_period_document(
            metadata,
            family,
            party,
            bank_counterparties=bank_parties,
            shared_period_title_terms=shared_period_title_terms,
        ),
    )


def _document_families(
    document_families: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    source = document_families or _DOCUMENT_FAMILIES
    return {_normalize_family_name(family): dict(settings) for family, settings in source.items()}


def _normalize_family_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _family_aliases(
    document_families: dict[str, dict[str, Any]] | None = None,
) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    for family, settings in _document_families(document_families).items():
        family_aliases = {family, family.replace("_", "-")}
        family_aliases.update(str(alias).strip().lower() for alias in settings.get("aliases", ()) or ())
        for alias in family_aliases:
            if alias:
                aliases.setdefault(alias, set()).add(family)
    return aliases


def _family_sequence(settings: dict[str, Any], key: str) -> tuple[str, ...]:
    value = settings.get(key, ())
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in (value or ()))


def _matches_family(doc_type: str, settings: dict[str, Any]) -> bool:
    if not doc_type:
        return False
    types = {clean_enum_string(item, "DocumentType").strip().lower() for item in _family_sequence(settings, "types")}
    if doc_type in types:
        return True
    prefixes = tuple(clean_enum_string(item, "DocumentType").strip().lower() for item in _family_sequence(settings, "prefixes"))
    return any(prefix and doc_type.startswith(prefix) for prefix in prefixes)


def _is_zero_amount_supplier_doc(doc_type: str, amount: Any, supplier_config: dict[str, Any]) -> bool:
    if not supplier_config.get("ignore_when_zero_amount", False):
        return False
    if not _matches_family(doc_type, supplier_config):
        return False
    try:
        return float(amount) == 0
    except (TypeError, ValueError):
        return False


def _is_shared_period_document(
    metadata: dict[str, Any],
    family: str,
    party: str,
    *,
    bank_counterparties: set[str] | None = None,
    shared_period_title_terms: dict[str, list[str]] | dict[str, tuple[str, ...]] | None = None,
) -> bool:
    if family != SUPPLIER_EVIDENCE:
        return False
    title = _compact(metadata.get("document_title"))
    raw_type = _compact(metadata.get("document_type_raw"))
    terms_by_party = _shared_period_terms(shared_period_title_terms)
    text = f"{title} {raw_type}"
    if any(term in text for term in terms_by_party.get(party, ())):
        return True
    bank_parties = bank_counterparties or _BANK_COUNTERPARTIES
    if party in bank_parties and any(term in text for term in terms_by_party.get("$bank", ())):
        return True
    return False


def _alias_for_value(value: Any, counterparty_aliases: dict[str, str] | None = None) -> str | None:
    normalized = _compact(value)
    if not normalized:
        return None
    return _counterparty_aliases(counterparty_aliases).get(normalized)


def _counterparty_aliases(counterparty_aliases: dict[str, str] | None = None) -> dict[str, str]:
    if not counterparty_aliases:
        return _COUNTERPARTY_ALIASES
    merged = dict(_COUNTERPARTY_ALIASES)
    for alias, canonical in counterparty_aliases.items():
        normalized = _compact(alias)
        if normalized and canonical:
            merged[normalized] = canonical
    return merged


def _shared_period_terms(
    terms: dict[str, list[str]] | dict[str, tuple[str, ...]] | None = None,
) -> dict[str, tuple[str, ...]]:
    if not terms:
        return _SHARED_PERIOD_TITLE_TERMS
    merged = dict(_SHARED_PERIOD_TITLE_TERMS)
    for party, values in terms.items():
        merged[party] = tuple(_compact(value) for value in values if _compact(value))
    return merged


def _normalize_tax_number(
    value: Any,
    *,
    default_country_prefix: str | None = DEFAULT_TAX_NUMBER_DEFAULT_COUNTRY_PREFIX,
) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    text = re.sub(r"[^A-Z0-9]", "", text)
    if default_country_prefix and re.fullmatch(r"\d{9}", text):
        return f"{default_country_prefix.upper()}{text}"
    return text


def _compact(value: Any) -> str:
    return compact_match_key(value)


def _slug(value: Any) -> str:
    text = strip_diacritics(str(value or "")).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")
