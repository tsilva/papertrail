"""Derived reconciliation evidence fields.

These helpers intentionally do not rewrite historical sidecars. They compute a
smaller reconciliation view from the existing metadata surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from papertrail.models import clean_enum_string
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
_BANK_COUNTERPARTIES = {"bpi", "millennium-bcp"}

_COUNTERPARTY_ALIASES = {
    "TESTBANKALPHATAX": "millennium-bcp",
    "ptTESTBANKALPHATAX": "millennium-bcp",
    "bancocomercialportugues": "millennium-bcp",
    "millenniumbcp": "millennium-bcp",
    "millenniumbcpsa": "millennium-bcp",
    "millenniumbcpbancocomercialportugues": "millennium-bcp",
    "millennium": "millennium-bcp",
    "millenniumbanco": "millennium-bcp",
    "bcp": "millennium-bcp",
    "TESTBANKBETATAX": "bpi",
    "ptTESTBANKBETATAX": "bpi",
    "bpi": "bpi",
    "bancobpi": "bpi",
    "bancobpisa": "bpi",
    "TESTSHAREDTOLL": "shared-toll",
    "ptTESTSHAREDTOLL": "shared-toll",
    "sharedtoll": "shared-toll",
    "sharedtollportugal": "shared-toll",
    "google": "google",
    "googlecommerce": "google",
    "googlecommercelimited": "google",
    "googley": "google",
    "ie9825613n": "google",
    "TESTUTILITY": "utility-provider",
    "ptTESTUTILITY": "utility-provider",
    "utility-provider": "utility-provider",
    "utility-providerportugal": "utility-provider",
    "utility-providerportugalcomunicacoespessoais": "utility-provider",
    "TESTINSURER": "insurance-provider",
    "ptTESTINSURER": "insurance-provider",
    "insurance-provider": "insurance-provider",
    "companhiadesegurosinsurance-providerportugal": "insurance-provider",
    "companhiadesegurosinsurance-providerportugalsa": "insurance-provider",
    "ISSUER-TAX-ID": "melo-nadais",
    "ptISSUER-TAX-ID": "melo-nadais",
    "melonadais": "melo-nadais",
    "melonadaisassociados": "melo-nadais",
    "500918880": "fidelidade",
    "pt500918880": "fidelidade",
    "fidelidade": "fidelidade",
    "companhiades": "fidelidade",
    "companhiadessegurosfidelidade": "fidelidade",
    "segurancasocial": "seguranca-social",
    "at": "at",
    "atautoridadetributariaeaduaneira": "at",
    "benefits-provider": "benefits-provider",
    "TESTCOMPANY": "example-company",
    "ptTESTCOMPANY": "example-company",
    "examplecompany": "example-company",
    "examplecompanyunipessoalltda": "example-company",
    "examplecompanyunipessoallda": "example-company",
    "digitalocean": "digitalocean",
    "digitaloceanllc": "digitalocean",
    "eu528002224": "digitalocean",
    "wisdomtree": "wisdomtree",
    "wisdomtreeuklimited": "wisdomtree",
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


def counterparty_id(metadata: dict[str, Any]) -> str:
    candidates = [
        metadata.get("issuer_tax_number"),
        metadata.get("issuing_party"),
        metadata.get("issuing_party_raw"),
    ]
    for value in candidates:
        alias = _alias_for_value(value)
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


def build_document_evidence(metadata: dict[str, Any]) -> DocumentEvidence:
    family = document_family_for_type(metadata.get("document_type"), metadata)
    party = counterparty_id(metadata)
    source_bank = party if family == BANK_ANCHOR and party in _BANK_COUNTERPARTIES else None
    return DocumentEvidence(
        document_family=family,
        counterparty_id=party,
        source_bank=source_bank,
        is_shared_period_document=_is_shared_period_document(metadata, family, party),
    )


def _is_zero_amount_supplier_doc(doc_type: str, amount: Any) -> bool:
    if not (doc_type in _SUPPLIER_TYPES or doc_type.startswith("invoice-") or doc_type.startswith("receipt-")):
        return False
    try:
        return float(amount) == 0
    except (TypeError, ValueError):
        return False


def _is_shared_period_document(metadata: dict[str, Any], family: str, party: str) -> bool:
    if family != SUPPLIER_EVIDENCE:
        return False
    title = _compact(metadata.get("document_title"))
    raw_type = _compact(metadata.get("document_type_raw"))
    if party == "shared-toll" and any(term in title for term in ("pagamentosdeservicos", "extratorecibo")):
        return True
    if party in _BANK_COUNTERPARTIES and any(
        term in f"{title} {raw_type}"
        for term in ("comissoes", "manctapacote", "operacaocartoes", "impostodoselo")
    ):
        return True
    return False


def _alias_for_value(value: Any) -> str | None:
    normalized = _compact(value)
    if not normalized:
        return None
    return _COUNTERPARTY_ALIASES.get(normalized)


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
