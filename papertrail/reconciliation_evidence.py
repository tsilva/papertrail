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
    DEFAULT_SHARED_PERIOD_TITLE_TERMS,
)
from papertrail.utils import strip_diacritics


BANK_ANCHOR = "bank_anchor"
SUPPLIER_EVIDENCE = "supplier_evidence"
TAX_EVIDENCE = "tax_evidence"
PAYROLL_EVIDENCE = "payroll_evidence"
LOAN_EVIDENCE = "loan_evidence"
INVESTMENT_EVIDENCE = "investment_evidence"
CONTRACT_EVIDENCE = "contract_evidence"
IGNORE = "ignore"
UNKNOWN = "unknown"

FAMILY_ALIASES = {
    "bank-anchor": {BANK_ANCHOR},
    "bank_anchor": {BANK_ANCHOR},
    "supplier-evidence": {SUPPLIER_EVIDENCE},
    "supplier_evidence": {SUPPLIER_EVIDENCE},
    "tax-evidence": {TAX_EVIDENCE},
    "tax_evidence": {TAX_EVIDENCE},
    "payroll-evidence": {PAYROLL_EVIDENCE},
    "payroll_evidence": {PAYROLL_EVIDENCE},
    "loan-evidence": {LOAN_EVIDENCE},
    "loan_evidence": {LOAN_EVIDENCE},
    "investment-evidence": {INVESTMENT_EVIDENCE},
    "investment_evidence": {INVESTMENT_EVIDENCE},
    "contract-evidence": {CONTRACT_EVIDENCE},
    "contract_evidence": {CONTRACT_EVIDENCE},
}

_BANK_ANCHOR_TYPES = {"bank-note", "bank-transfer", "bank-card-transaction"}
_SUPPLIER_TYPES = {
    "invoice",
    "receipt",
    "invoice-receipt",
    "invoice-credit",
    "invoice-debit",
    "insurance-notice",
    "bank-fees",
}
_INVESTMENT_TYPES = {
    "bank-investment",
    "investment-acquisition-summary",
    "bank-stock-buy",
    "bank-stock-sell",
}
_IGNORE_TYPES = {"investment-key-information-document", "loan-simulation"}
_BANK_COUNTERPARTIES = set(DEFAULT_BANK_COUNTERPARTIES)
_COUNTERPARTY_ALIASES = dict(DEFAULT_COUNTERPARTY_ALIASES)
_SHARED_PERIOD_TITLE_TERMS = {
    party: tuple(terms)
    for party, terms in DEFAULT_SHARED_PERIOD_TITLE_TERMS.items()
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


def document_family_for_type(doc_type: str | None, metadata: dict[str, Any] | None = None) -> str:
    doc_type = clean_enum_string(doc_type or "", "DocumentType").strip().lower()
    metadata = metadata or {}

    if doc_type in _IGNORE_TYPES:
        return IGNORE
    if _is_zero_amount_supplier_doc(doc_type, metadata.get("total_amount")):
        return IGNORE
    if doc_type in _BANK_ANCHOR_TYPES:
        return BANK_ANCHOR
    if doc_type in _SUPPLIER_TYPES or doc_type.startswith("invoice-") or doc_type.startswith("receipt-"):
        return SUPPLIER_EVIDENCE
    if doc_type.startswith("tax-"):
        return TAX_EVIDENCE
    if doc_type.startswith("payroll-"):
        return PAYROLL_EVIDENCE
    if doc_type.startswith("loan-"):
        return LOAN_EVIDENCE
    if doc_type in _INVESTMENT_TYPES:
        return INVESTMENT_EVIDENCE
    if doc_type == "contract-signup":
        return CONTRACT_EVIDENCE
    if not doc_type:
        return UNKNOWN
    return UNKNOWN


def document_type_matches_family(doc_type: str | None, family_pattern: str) -> bool:
    families = FAMILY_ALIASES.get(family_pattern.strip().lower())
    if not families:
        return False
    return document_family_for_type(doc_type) in families


def counterparty_id(
    metadata: dict[str, Any],
    *,
    counterparty_aliases: dict[str, str] | None = None,
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

    tax_number = _normalize_tax_number(metadata.get("issuer_tax_number"))
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
) -> DocumentEvidence:
    family = document_family_for_type(metadata.get("document_type"), metadata)
    party = counterparty_id(metadata, counterparty_aliases=counterparty_aliases)
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


def _is_zero_amount_supplier_doc(doc_type: str, amount: Any) -> bool:
    if not (doc_type in _SUPPLIER_TYPES or doc_type.startswith("invoice-") or doc_type.startswith("receipt-")):
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


def _normalize_tax_number(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    text = re.sub(r"[^A-Z0-9]", "", text)
    if re.fullmatch(r"\d{9}", text):
        return f"PT{text}"
    return text


def _compact(value: Any) -> str:
    return "".join(char for char in strip_diacritics(str(value or "")).lower() if char.isalnum())


def _slug(value: Any) -> str:
    text = strip_diacritics(str(value or "")).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")
