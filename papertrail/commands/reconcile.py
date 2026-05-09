"""Bank transaction reconciliation commands."""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import fitz

from papertrail.bank_statement.extractor import load_transactions as load_bank_statement_transactions
from papertrail.config import ReconciliationRule
from papertrail.document_types import normalize_document_type
from papertrail.llm import _extract_json_from_response
from papertrail.logging_utils import get_logger
from papertrail.reconciliation_defaults import (
    DEFAULT_AMOUNT_TOLERANCE,
    DEFAULT_BANK_COUNTERPARTIES,
    DEFAULT_BANK_EXPORT_PREFIX,
    DEFAULT_BANK_GENERATED_DOC_TYPES,
    DEFAULT_COUNTERPARTY_ALIASES,
    DEFAULT_DATE_WINDOW_DAYS,
    DEFAULT_DOCUMENT_FAMILIES,
    DEFAULT_EVIDENCE_COUNTERPARTY_AMOUNT_OPTIONAL_CATEGORIES,
    DEFAULT_EVIDENCE_COUNTERPARTY_CATEGORIES,
    DEFAULT_EVIDENCE_COUNTERPARTY_REQUIRED_PATTERN,
    DEFAULT_EVIDENCE_COUNTERPARTY_SKIP_IF_SUPPLIER_PRESENT_CATEGORIES,
    DEFAULT_LINE_ITEM_CATEGORY_ALIASES,
    DEFAULT_LINE_ITEM_EXTRACTORS,
    DEFAULT_LLM_MATCH_CONFIDENCE,
    DEFAULT_RECONCILIATION_CURRENCY,
    DEFAULT_RECONCILIATION_RULES,
    DEFAULT_SAME_MONTH_SHARED_RULE_NAMES,
    DEFAULT_SHARED_PERIOD_BANK_ANCHOR_ERROR_EXEMPT_RULE_NAMES,
    DEFAULT_SHARED_PERIOD_LINK_CATEGORIES,
    DEFAULT_SHARED_PERIOD_SUPPLIER_EVIDENCE_ERROR_EXEMPT_RULE_NAMES,
    DEFAULT_SHARED_PERIOD_TITLE_TERMS,
    DEFAULT_SHARED_PERIOD_TRANSACTION_KEYWORDS,
    DEFAULT_STATEMENT_BANK_ISSUER_ALIASES,
    DEFAULT_STATEMENT_BANK_SCOPED_DOC_TYPES,
    DEFAULT_STRICT_STATEMENT_BANKS,
    DEFAULT_SUPPORTING_DOC_TYPE_PATTERNS,
    DEFAULT_SUPPORTING_EXPORT_PREFIXES,
    DEFAULT_SUPPORTING_PAIR_EXEMPT_STATEMENT_BANKS,
    DEFAULT_TAX_NUMBER_DEFAULT_COUNTRY_PREFIX,
)
from papertrail.reconciliation_evidence import build_document_evidence
from papertrail.repository import DocumentRepository
from papertrail.rules import RuleEngine
from papertrail.runtime import Runtime
from papertrail.utils import compact_match_key, strip_diacritics

logger = get_logger("reconcile")


class LineItemExtractionError(RuntimeError):
    """Raised when a relevant PDF cannot be read for line-item extraction."""


_AMOUNT_LINE_RE = re.compile(r"^-?\d[\d\s.]*,\d{2}$")
_PERIOD_TOKEN_RE = re.compile(
    r"\b(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+(20\d{2})\b"
)
_DATE_DMY_RE = re.compile(r"\b(\d{2})[/-](\d{2})[/-](20\d{2})\b")
_DATE_DM_RE = re.compile(r"\b(\d{2})[/-](\d{2})\b")


@dataclass(frozen=True)
class ReconciliationPolicy:
    amount_tolerance: float = DEFAULT_AMOUNT_TOLERANCE
    date_window_days: int = DEFAULT_DATE_WINDOW_DAYS
    tax_number_default_country_prefix: str = DEFAULT_TAX_NUMBER_DEFAULT_COUNTRY_PREFIX
    bank_generated_doc_types: tuple[str, ...] = DEFAULT_BANK_GENERATED_DOC_TYPES
    statement_bank_scoped_doc_types: tuple[str, ...] = DEFAULT_STATEMENT_BANK_SCOPED_DOC_TYPES
    statement_bank_issuer_aliases: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_STATEMENT_BANK_ISSUER_ALIASES)
    )
    bank_export_prefix: str = DEFAULT_BANK_EXPORT_PREFIX
    supporting_export_prefixes: tuple[str, ...] = DEFAULT_SUPPORTING_EXPORT_PREFIXES
    supporting_doc_type_patterns: tuple[str, ...] = DEFAULT_SUPPORTING_DOC_TYPE_PATTERNS
    document_families: dict[str, dict[str, object]] = field(
        default_factory=lambda: {
            family: dict(settings)
            for family, settings in DEFAULT_DOCUMENT_FAMILIES.items()
            if isinstance(settings, dict)
        }
    )
    bank_counterparties: tuple[str, ...] = DEFAULT_BANK_COUNTERPARTIES
    counterparty_aliases: dict[str, str] = field(default_factory=dict)
    shared_period_transaction_keywords: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            party: tuple(keywords)
            for party, keywords in DEFAULT_SHARED_PERIOD_TRANSACTION_KEYWORDS.items()
        }
    )
    shared_period_title_terms: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            party: tuple(terms)
            for party, terms in DEFAULT_SHARED_PERIOD_TITLE_TERMS.items()
        }
    )
    same_month_shared_rule_names: tuple[str, ...] = DEFAULT_SAME_MONTH_SHARED_RULE_NAMES
    strict_statement_banks: tuple[str, ...] = DEFAULT_STRICT_STATEMENT_BANKS
    supporting_pair_exempt_statement_banks: tuple[str, ...] = (
        DEFAULT_SUPPORTING_PAIR_EXEMPT_STATEMENT_BANKS
    )
    shared_period_link_categories: tuple[str, ...] = DEFAULT_SHARED_PERIOD_LINK_CATEGORIES
    shared_period_supplier_evidence_error_exempt_rule_names: tuple[str, ...] = (
        DEFAULT_SHARED_PERIOD_SUPPLIER_EVIDENCE_ERROR_EXEMPT_RULE_NAMES
    )
    shared_period_bank_anchor_error_exempt_rule_names: tuple[str, ...] = (
        DEFAULT_SHARED_PERIOD_BANK_ANCHOR_ERROR_EXEMPT_RULE_NAMES
    )
    evidence_counterparty_categories: tuple[str, ...] = DEFAULT_EVIDENCE_COUNTERPARTY_CATEGORIES
    evidence_counterparty_required_pattern: str = DEFAULT_EVIDENCE_COUNTERPARTY_REQUIRED_PATTERN
    evidence_counterparty_skip_if_supplier_present_categories: tuple[str, ...] = (
        DEFAULT_EVIDENCE_COUNTERPARTY_SKIP_IF_SUPPLIER_PRESENT_CATEGORIES
    )
    evidence_counterparty_amount_optional_categories: tuple[str, ...] = (
        DEFAULT_EVIDENCE_COUNTERPARTY_AMOUNT_OPTIONAL_CATEGORIES
    )
    default_currency: str = DEFAULT_RECONCILIATION_CURRENCY
    llm_default_confidence: float = DEFAULT_LLM_MATCH_CONFIDENCE
    line_item_category_aliases: dict[str, dict[str, object]] = field(
        default_factory=lambda: {
            name: dict(settings)
            for name, settings in DEFAULT_LINE_ITEM_CATEGORY_ALIASES.items()
        }
    )
    line_item_extractors: dict[str, dict[str, object]] = field(
        default_factory=lambda: {
            name: dict(settings)
            for name, settings in DEFAULT_LINE_ITEM_EXTRACTORS.items()
        }
    )


_ACTIVE_RECONCILIATION_POLICY: ContextVar[ReconciliationPolicy] = ContextVar(
    "papertrail_reconciliation_policy",
    default=ReconciliationPolicy(),
)


def _reconciliation_policy() -> ReconciliationPolicy:
    return _ACTIVE_RECONCILIATION_POLICY.get()


def _rule_engine() -> RuleEngine:
    return RuleEngine(document_families=_reconciliation_policy().document_families)


def _amount_tolerance() -> float:
    return _reconciliation_policy().amount_tolerance


def _date_window_days() -> int:
    return _reconciliation_policy().date_window_days


def _default_currency() -> str:
    return _reconciliation_policy().default_currency


def _sequence(value, fallback: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if value is None:
        return tuple(fallback)
    return tuple(str(item) for item in value)


def _string_map(value, fallback: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(fallback or {})
    if not value:
        return merged
    for key, item in dict(value).items():
        if key and item:
            merged[str(key)] = str(item)
    return merged


def _keywords_map(value, fallback: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    merged = dict(fallback)
    if not value:
        return merged
    for key, items in dict(value).items():
        if key and items:
            merged[str(key)] = tuple(str(item) for item in items)
    return merged


def _settings_map(value, fallback: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    merged = {name: dict(settings) for name, settings in fallback.items()}
    if not value:
        return merged
    for name, settings in dict(value).items():
        if not isinstance(settings, dict):
            continue
        merged[str(name)] = {**merged.get(str(name), {}), **settings}
    return merged


def _settings_map_with_optional_builtin(
    value,
    fallback: dict[str, dict[str, object]],
    *,
    include_builtin: bool,
) -> dict[str, dict[str, object]]:
    return _settings_map(value, fallback if include_builtin else {})


def _policy_from_profile(profile) -> ReconciliationPolicy:
    settings = profile.reconciliation
    statement_aliases = {
        _normalize_for_match(alias): canonical
        for alias, canonical in _string_map(
            getattr(settings, "statement_bank_issuer_aliases", None),
            DEFAULT_STATEMENT_BANK_ISSUER_ALIASES,
        ).items()
    }
    return ReconciliationPolicy(
        amount_tolerance=float(getattr(settings, "amount_tolerance", DEFAULT_AMOUNT_TOLERANCE)),
        date_window_days=int(getattr(settings, "date_window_days", DEFAULT_DATE_WINDOW_DAYS)),
        tax_number_default_country_prefix=str(
            getattr(
                settings,
                "tax_number_default_country_prefix",
                DEFAULT_TAX_NUMBER_DEFAULT_COUNTRY_PREFIX,
            )
            or ""
        ),
        bank_generated_doc_types=_sequence(
            getattr(settings, "bank_generated_doc_types", None),
            DEFAULT_BANK_GENERATED_DOC_TYPES,
        ),
        statement_bank_scoped_doc_types=_sequence(
            getattr(settings, "statement_bank_scoped_doc_types", None),
            DEFAULT_STATEMENT_BANK_SCOPED_DOC_TYPES,
        ),
        statement_bank_issuer_aliases=statement_aliases,
        bank_export_prefix=str(getattr(settings, "bank_export_prefix", DEFAULT_BANK_EXPORT_PREFIX)),
        supporting_export_prefixes=_sequence(
            getattr(settings, "supporting_export_prefixes", None),
            DEFAULT_SUPPORTING_EXPORT_PREFIXES,
        ),
        supporting_doc_type_patterns=_sequence(
            getattr(settings, "supporting_doc_type_patterns", None),
            DEFAULT_SUPPORTING_DOC_TYPE_PATTERNS,
        ),
        document_families=_settings_map(
            getattr(settings, "document_families", None),
            DEFAULT_DOCUMENT_FAMILIES,
        ),
        bank_counterparties=_sequence(
            getattr(settings, "bank_counterparties", None),
            DEFAULT_BANK_COUNTERPARTIES,
        ),
        counterparty_aliases=_string_map(
            getattr(settings, "counterparty_aliases", None),
            DEFAULT_COUNTERPARTY_ALIASES,
        ),
        shared_period_transaction_keywords=_keywords_map(
            getattr(settings, "shared_period_transaction_keywords", None),
            DEFAULT_SHARED_PERIOD_TRANSACTION_KEYWORDS,
        ),
        shared_period_title_terms=_keywords_map(
            getattr(settings, "shared_period_title_terms", None),
            DEFAULT_SHARED_PERIOD_TITLE_TERMS,
        ),
        same_month_shared_rule_names=_sequence(
            getattr(settings, "same_month_shared_rule_names", None),
            DEFAULT_SAME_MONTH_SHARED_RULE_NAMES,
        ),
        strict_statement_banks=_sequence(
            getattr(settings, "strict_statement_banks", None),
            DEFAULT_STRICT_STATEMENT_BANKS,
        ),
        supporting_pair_exempt_statement_banks=_sequence(
            getattr(settings, "supporting_pair_exempt_statement_banks", None),
            DEFAULT_SUPPORTING_PAIR_EXEMPT_STATEMENT_BANKS,
        ),
        shared_period_link_categories=_sequence(
            getattr(settings, "shared_period_link_categories", None),
            DEFAULT_SHARED_PERIOD_LINK_CATEGORIES,
        ),
        shared_period_supplier_evidence_error_exempt_rule_names=_sequence(
            getattr(settings, "shared_period_supplier_evidence_error_exempt_rule_names", None),
            DEFAULT_SHARED_PERIOD_SUPPLIER_EVIDENCE_ERROR_EXEMPT_RULE_NAMES,
        ),
        shared_period_bank_anchor_error_exempt_rule_names=_sequence(
            getattr(settings, "shared_period_bank_anchor_error_exempt_rule_names", None),
            DEFAULT_SHARED_PERIOD_BANK_ANCHOR_ERROR_EXEMPT_RULE_NAMES,
        ),
        evidence_counterparty_categories=_sequence(
            getattr(settings, "evidence_counterparty_categories", None),
            DEFAULT_EVIDENCE_COUNTERPARTY_CATEGORIES,
        ),
        evidence_counterparty_required_pattern=str(
            getattr(
                settings,
                "evidence_counterparty_required_pattern",
                DEFAULT_EVIDENCE_COUNTERPARTY_REQUIRED_PATTERN,
            )
        ),
        evidence_counterparty_skip_if_supplier_present_categories=_sequence(
            getattr(settings, "evidence_counterparty_skip_if_supplier_present_categories", None),
            DEFAULT_EVIDENCE_COUNTERPARTY_SKIP_IF_SUPPLIER_PRESENT_CATEGORIES,
        ),
        evidence_counterparty_amount_optional_categories=_sequence(
            getattr(settings, "evidence_counterparty_amount_optional_categories", None),
            DEFAULT_EVIDENCE_COUNTERPARTY_AMOUNT_OPTIONAL_CATEGORIES,
        ),
        default_currency=str(
            getattr(settings, "default_currency", DEFAULT_RECONCILIATION_CURRENCY)
            or DEFAULT_RECONCILIATION_CURRENCY
        ),
        llm_default_confidence=float(
            getattr(settings, "llm_default_confidence", DEFAULT_LLM_MATCH_CONFIDENCE)
            or DEFAULT_LLM_MATCH_CONFIDENCE
        ),
        line_item_category_aliases=_settings_map(
            getattr(settings, "line_item_category_aliases", None),
            DEFAULT_LINE_ITEM_CATEGORY_ALIASES,
        ),
        line_item_extractors=_settings_map_with_optional_builtin(
            getattr(settings, "line_item_extractors", None),
            DEFAULT_LINE_ITEM_EXTRACTORS,
            include_builtin=getattr(settings, "include_builtin_line_item_extractors", True),
        ),
    )


def _reconciliation_rules() -> list[ReconciliationRule]:
    """Broad default reconciliation rules built on derived evidence families."""
    return [ReconciliationRule.model_validate(rule) for rule in DEFAULT_RECONCILIATION_RULES]


def _rules_from_profile(profile) -> list[ReconciliationRule]:
    configured_rules = getattr(profile.reconciliation, "rules", None) or []
    if not configured_rules:
        if not getattr(profile.reconciliation, "include_builtin_rules", True):
            return []
        return _reconciliation_rules()
    return [
        rule if isinstance(rule, ReconciliationRule) else ReconciliationRule.model_validate(rule)
        for rule in configured_rules
    ]


def _line_item_config(name: str) -> dict[str, object]:
    return dict(_reconciliation_policy().line_item_extractors.get(name, {}))


def _config_sequence(config: dict[str, object], key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = config.get(key, default)
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in (value or ()))


def _config_float(config: dict[str, object], key: str, default: float) -> float:
    try:
        return float(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _config_int(config: dict[str, object], key: str, default: int) -> int:
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _config_regex(config: dict[str, object], key: str, *, flags: int = 0) -> re.Pattern[str] | None:
    pattern = config.get(key)
    if not pattern:
        return None
    return re.compile(str(pattern), flags)


def _doc_matches_line_item_config(doc_type: str, issuing_party: str, config: dict[str, object]) -> bool:
    doc_types = {item.lower() for item in _config_sequence(config, "document_types")}
    issuing_parties = {_normalize_for_match(item) for item in _config_sequence(config, "issuing_parties")}
    return (
        bool(doc_types)
        and bool(issuing_parties)
        and doc_type in doc_types
        and issuing_party in issuing_parties
    )


@dataclass(frozen=True)
class CandidateLineItem:
    category: str
    amount: float
    label: str
    date_issued: Optional[str] = None
    reference: Optional[str] = None
    amount_currency: Optional[str] = None
    amount_match_required: bool = True
    document_type: Optional[str] = None


@dataclass(frozen=True)
class MatchedLineItem:
    candidate: "PDFCandidate"
    line_item: CandidateLineItem


@dataclass
class Transaction:
    row_number: int
    date_posting: Optional[str]
    date_value: Optional[str]
    description: str
    amount: float
    currency: str
    notes: str
    treated: str


@dataclass
class PDFCandidate:
    json_path: Path
    pdf_filename: str
    date_issued: Optional[str]
    document_type: Optional[str]
    document_type_raw: Optional[str]
    document_title: Optional[str]
    issuing_party: Optional[str]
    total_amount: Optional[float]
    total_amount_currency: Optional[str]
    page_count: Optional[int] = None
    file_extension: Optional[str] = None
    hash_file: Optional[str] = None
    sub_doc_index: Optional[int] = None
    is_sub_document: bool = False
    exclude_from_matching: bool = False
    document_family: str = "unknown"
    counterparty_id: str = "$UNKNOWN$"
    source_bank: Optional[str] = None
    source_filename: Optional[str] = None
    is_bank_anchor: bool = False
    is_supplier_evidence: bool = False
    is_ignored_for_reconciliation: bool = False
    is_shared_period_document: bool = False
    line_items: list[CandidateLineItem] = field(default_factory=list)

    @property
    def candidate_id(self) -> str:
        base_id = self.hash_file or str(self.json_path)
        if self.sub_doc_index is not None:
            return f"{base_id}#sub{self.sub_doc_index}"
        return base_id

    @property
    def effective_document_type(self) -> Optional[str]:
        if (self.file_extension or "").lower() == ".pdf":
            return normalize_document_type(self.document_type, self.document_type_raw, self.document_title)
        return self.document_type


@dataclass
class MatchResult:
    transaction: Transaction
    pdf_candidates: list[PDFCandidate] = field(default_factory=list)
    method: str = ""
    confidence: float = 0.0
    reasoning: str = ""
    line_items: list[MatchedLineItem] = field(default_factory=list)


def _coerce_amount(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_candidate(
    json_path: Path,
    pdf_filename: str,
    data: dict,
    *,
    document_title: Optional[str],
    page_count: Optional[int] = None,
    file_extension: Optional[str] = None,
    sub_doc_index: Optional[int] = None,
    is_sub_document: bool = False,
    exclude_from_matching: bool = False,
    line_items: list[CandidateLineItem] | None = None,
    policy: ReconciliationPolicy | None = None,
) -> PDFCandidate:
    policy = policy or _reconciliation_policy()
    evidence = build_document_evidence(
        data,
        counterparty_aliases=policy.counterparty_aliases,
        bank_counterparties=policy.bank_counterparties,
        shared_period_title_terms=policy.shared_period_title_terms,
        document_families=policy.document_families,
        tax_number_default_country_prefix=policy.tax_number_default_country_prefix,
    )
    return PDFCandidate(
        json_path=json_path,
        pdf_filename=pdf_filename,
        date_issued=data.get("date_issued"),
        document_type=data.get("document_type"),
        document_type_raw=data.get("document_type_raw"),
        document_title=document_title,
        issuing_party=data.get("issuing_party"),
        total_amount=_coerce_amount(data.get("total_amount")),
        total_amount_currency=data.get("total_amount_currency"),
        page_count=page_count,
        file_extension=file_extension,
        hash_file=data.get("hash_file"),
        sub_doc_index=sub_doc_index,
        is_sub_document=is_sub_document,
        exclude_from_matching=exclude_from_matching,
        document_family=evidence.document_family,
        counterparty_id=evidence.counterparty_id,
        source_bank=evidence.source_bank,
        source_filename=data.get("source_filename"),
        is_bank_anchor=evidence.is_bank_anchor,
        is_supplier_evidence=evidence.is_supplier_evidence,
        is_ignored_for_reconciliation=evidence.is_ignored_for_reconciliation,
        is_shared_period_document=evidence.is_shared_period_document,
        line_items=line_items or [],
    )


def _transaction_category(txn: Transaction, rules: list) -> str:
    return _classify_transaction(txn, rules)[0]


def _serialize_match(match: MatchResult, *, errors: dict[int, list[str]], rules: list) -> dict:
    txn = match.transaction
    data = {
        "row": txn.row_number,
        "date": txn.date_posting or txn.date_value,
        "description": txn.description,
        "amount": txn.amount,
        "currency": txn.currency,
        "transaction_category": _transaction_category(txn, rules),
        "method": match.method,
        "confidence": match.confidence,
        "reasoning": match.reasoning,
        "files": [candidate.pdf_filename for candidate in match.pdf_candidates],
        "errors": errors.get(txn.row_number, []),
    }
    if match.line_items:
        data["line_items"] = [
            {
                "file": item.candidate.pdf_filename,
                "category": item.line_item.category,
                "label": item.line_item.label,
                "amount": item.line_item.amount,
                "currency": item.line_item.amount_currency,
                "date": item.line_item.date_issued,
                "reference": item.line_item.reference,
                "document_type": item.line_item.document_type,
            }
            for item in match.line_items
        ]
    return data


def _serialize_unmatched_transaction(txn: Transaction, rules: list) -> dict:
    return {
        "row": txn.row_number,
        "date": txn.date_posting or txn.date_value,
        "description": txn.description,
        "amount": txn.amount,
        "currency": txn.currency,
        "transaction_category": _transaction_category(txn, rules),
    }


def _serialize_unmatched_candidate(cand: PDFCandidate) -> dict:
    return {
        "file": cand.pdf_filename,
        "date_issued": cand.date_issued,
        "document_type": cand.effective_document_type or cand.document_type,
        "issuing_party": cand.issuing_party,
        "total_amount": cand.total_amount,
        "currency": cand.total_amount_currency,
    }


def _parse_date(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


def _load_transactions(runtime: Runtime, excel_path: Path) -> list[Transaction]:
    return [
        Transaction(**data)
        for data in load_bank_statement_transactions(
            excel_path,
            settings=runtime.profile.bank_statements,
            strict=True,
        )
    ]


def _load_statement_issuing_party(repository: DocumentRepository, excel_path: Path) -> Optional[str]:
    json_path = excel_path.with_suffix(".json")
    if not json_path.exists():
        return None
    try:
        data = repository.load_metadata(json_path)
    except Exception as exc:
        logger.debug(f"[STATEMENT-BANK] Could not read {json_path.name}: {exc}")
        return None
    issuing_party = data.get("issuing_party")
    if not issuing_party or issuing_party == "$UNKNOWN$":
        return None
    return str(issuing_party)


def discover_bank_statements(repository: DocumentRepository, export_path: Path) -> list[Path]:
    statements = []
    for json_path, data in repository.iter_sidecars(export_path):
        if data.get("document_type") != "bank-statement":
            continue
        doc_path = repository.find_companion(json_path, data)
        if doc_path and doc_path.suffix.lower() == ".xlsx":
            statements.append(doc_path)
    return statements


def _reconciliation_search_paths(export_path: Path) -> list[Path]:
    paths = [export_path]
    parts = export_path.name.split("-")
    if len(parts) != 2:
        return paths

    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError:
        return paths

    if not (1 <= month <= 12):
        return paths

    def shift_month(base_year: int, base_month: int, delta: int) -> tuple[int, int]:
        month_index = (base_year * 12 + (base_month - 1)) + delta
        return month_index // 12, month_index % 12 + 1

    for delta in (-1, 1):
        neighbor_year, neighbor_month = shift_month(year, month, delta)
        neighbor = export_path.parent / f"{neighbor_year:04d}-{neighbor_month:02d}"
        if neighbor.exists():
            paths.append(neighbor)
    return paths


def _export_month_key(export_path: Path) -> Optional[tuple[int, int]]:
    parts = export_path.name.split("-")
    if len(parts) != 2:
        return None
    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError:
        return None
    if not (1 <= month <= 12):
        return None
    return year, month


def _prior_reconciliation_paths(export_path: Path) -> list[Path]:
    current_key = _export_month_key(export_path)
    if current_key is None:
        return []
    return [
        search_path
        for search_path in _reconciliation_search_paths(export_path)
        if (path_key := _export_month_key(search_path)) is not None
        and path_key < current_key
    ]


def _filename_hash_key(filename: str) -> Optional[str]:
    match = re.search(r"(?:^| - )([0-9a-fA-F]{8})(?:\.[^.]+)?$", filename)
    return match.group(1).lower() if match else None


def _load_reconciled_candidate_keys(search_paths: list[Path]) -> set[str]:
    reconciled: set[str] = set()
    for search_path in search_paths:
        for reconciliation_path in search_path.rglob("*.reconciliation.json"):
            try:
                relative_path = reconciliation_path.relative_to(search_path)
                if DocumentRepository.is_internal_path(relative_path):
                    continue
                data = json.loads(reconciliation_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                continue

            for match in data.get("matches", []):
                if match.get("errors"):
                    continue
                files = match.get("files") or []
                if isinstance(files, str):
                    files = [files]
                for filename in files:
                    if not filename:
                        continue
                    filename = str(filename)
                    reconciled.add(filename)
                    if hash_key := _filename_hash_key(filename):
                        reconciled.add(hash_key)
    return reconciled


def _is_prior_reconciled_candidate(candidate: PDFCandidate, prior_reconciled_keys: set[str]) -> bool:
    return bool(
        candidate.pdf_filename in prior_reconciled_keys
        or (candidate.hash_file and candidate.hash_file.lower() in prior_reconciled_keys)
        or candidate.candidate_id in prior_reconciled_keys
    )


def _date_from_iso(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _transaction_date_bounds(transactions: list[Transaction]) -> tuple[Optional[date], Optional[date]]:
    dates = [
        parsed
        for txn in transactions
        if (parsed := _date_from_iso(_parse_date(txn.date_posting or txn.date_value)))
    ]
    if not dates:
        return None, None
    return min(dates), max(dates)


def _relevant_companion_amounts(transactions: list[Transaction], rules: list) -> set[float]:
    relevant_amounts: set[float] = set()
    rules_with_companions = [rule for rule in rules if rule.companions]
    if not rules_with_companions:
        return relevant_amounts

    txns_by_rule: dict[str, list[Transaction]] = {}
    for txn in transactions:
        category, _ = _classify_transaction(txn, rules)
        txns_by_rule.setdefault(category, []).append(txn)

    seen_groups: set[frozenset[int]] = set()
    for rule in rules_with_companions:
        companion_names = [rule.name] + rule.companions
        companion_txns: list[Transaction] = []
        for name in companion_names:
            companion_txns.extend(txns_by_rule.get(name, []))
        if len(companion_txns) < 2:
            continue

        by_date: dict[str, list[Transaction]] = {}
        for txn in companion_txns:
            txn_date = txn.date_posting or txn.date_value
            if txn_date:
                by_date.setdefault(txn_date, []).append(txn)

        for group_txns in by_date.values():
            group_rules = {_classify_transaction(txn, rules)[0] for txn in group_txns}
            if len(group_rules) < 2:
                continue
            group_key = frozenset(txn.row_number for txn in group_txns)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            relevant_amounts.add(sum(abs(txn.amount) for txn in group_txns))

    return relevant_amounts


def _shared_requirements(transactions: list[Transaction], rules: list) -> list[tuple[str, Optional[str]]]:
    requirements: list[tuple[str, Optional[str]]] = []
    seen: set[tuple[str, Optional[str]]] = set()
    for txn in transactions:
        _, rule = _classify_transaction(txn, rules)
        if rule is None or not rule.shared_types:
            continue
        for type_pattern, issuing_party_filter in rule.shared_types.items():
            key = (type_pattern, issuing_party_filter)
            if key in seen:
                continue
            seen.add(key)
            requirements.append(key)
    return requirements


def _required_patterns(transactions: list[Transaction], rules: list) -> list[str]:
    patterns: list[str] = []
    seen: set[str] = set()
    for txn in transactions:
        _, rule = _classify_transaction(txn, rules)
        if rule is None:
            continue
        for pattern in list(rule.required_types.keys()) + list(rule.shared_types.keys()):
            if pattern in seen:
                continue
            seen.add(pattern)
            patterns.append(pattern)
    return patterns


def _candidate_matches_patterns(candidate: PDFCandidate, patterns: list[str]) -> bool:
    doc_type = candidate.effective_document_type or candidate.document_type
    if not doc_type:
        return False
    engine = _rule_engine()
    return any(engine.match_doc_type(doc_type, pattern) for pattern in patterns)


def _parse_euro_amount_line(line: str) -> Optional[float]:
    text = line.strip()
    if not _AMOUNT_LINE_RE.fullmatch(text):
        return None
    try:
        return float(text.replace(" ", "").replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _parse_amount_line(line: str) -> Optional[float]:
    amount = _parse_euro_amount_line(line)
    if amount is not None:
        return amount
    parsed = _parse_currency_amount_line(line)
    if parsed:
        return parsed[0]
    return None


def _parse_currency_amount_line(line: str) -> Optional[tuple[float, Optional[str]]]:
    text = line.strip()
    match = re.fullmatch(r"(-?\d[\d\s.]*,\d{2})(?:\s+([A-Z]{3}))?", text)
    if not match:
        return None
    try:
        amount = float(match.group(1).replace(" ", "").replace(".", "").replace(",", "."))
    except ValueError:
        return None
    return amount, match.group(2)


def _parse_currency_amounts_between(
    lines: list[str],
    start: int,
    end: int,
) -> list[tuple[float, Optional[str]]]:
    amount_entries: list[tuple[float, Optional[str]]] = []
    standalone_currencies: list[str] = []
    for line in lines[start:end]:
        parsed = _parse_currency_amount_line(line)
        if parsed:
            amount_entries.append(parsed)
            continue
        currency = line.strip()
        if re.fullmatch(r"[A-Z]{3}", currency):
            standalone_currencies.append(currency)

    if not standalone_currencies:
        return amount_entries

    filled_entries: list[tuple[float, Optional[str]]] = []
    for index, (amount, currency) in enumerate(amount_entries):
        if currency is None:
            if index < len(standalone_currencies):
                currency = standalone_currencies[index]
            else:
                currency = standalone_currencies[0]
        filled_entries.append((amount, currency))
    return filled_entries


def _amounts_near(lines: list[str], index: int, *, before: int = 0, after: int = 0) -> list[float]:
    start = max(0, index - before)
    end = min(len(lines), index + after + 1)
    amounts: list[float] = []
    for line in lines[start:end]:
        amount = _parse_amount_line(line)
        if amount is not None:
            amounts.append(amount)
    return amounts


def _find_bpi_stamp_duty_pair(
    amounts: list[float],
    *,
    rate: float,
    max_tax: float,
) -> tuple[float, float] | None:
    pairs: list[tuple[int, float, float]] = []
    for gross_index, gross in enumerate(amounts):
        for base in amounts:
            if gross <= base:
                continue
            tax = round(gross - base, 2)
            if tax <= 0 or tax > max_tax:
                continue
            if abs(round(base * rate, 2) - tax) <= _amount_tolerance():
                pairs.append((gross_index, base, gross))
    if not pairs:
        return None
    _, base, gross = min(pairs, key=lambda item: (item[0], item[1]))
    return base, gross


def _append_line_item(
    line_items: list[CandidateLineItem],
    seen: set[tuple[str, float, str]],
    *,
    category: str,
    amount: float,
    label: str,
    date_issued: Optional[str] = None,
    reference: Optional[str] = None,
    amount_currency: Optional[str] = None,
    amount_match_required: bool = True,
    document_type: Optional[str] = None,
) -> None:
    rounded = round(amount, 2)
    key = (category, rounded, label)
    if rounded <= 0 or key in seen:
        return
    seen.add(key)
    line_items.append(
        CandidateLineItem(
            category=category,
            amount=rounded,
            label=label,
            date_issued=date_issued,
            reference=reference,
            amount_currency=amount_currency,
            amount_match_required=amount_match_required,
            document_type=document_type,
        )
    )


def _extract_bpi_fee_invoice_line_items(pdf_path: Path, data: dict) -> list[CandidateLineItem]:
    config = _line_item_config("bpi_fee_invoice")
    doc_type = (data.get("document_type") or "").lower()
    issuing_party = _normalize_for_match(data.get("issuing_party") or "")
    title = _normalize_for_match(data.get("document_title") or "")
    if not _doc_matches_line_item_config(doc_type, issuing_party, config):
        return []
    title_terms = tuple(_normalize_for_match(item) for item in _config_sequence(config, "title_terms"))
    if title_terms and not any(term in title for term in title_terms):
        return []

    try:
        with fitz.open(pdf_path) as pdf:
            lines = [line.strip() for page in pdf for line in page.get_text().splitlines() if line.strip()]
    except Exception as exc:
        raise LineItemExtractionError(f"Failed to read {pdf_path.name}: {exc}") from exc

    normalized_lines = [strip_diacritics(line).upper() for line in lines]
    line_items: list[CandidateLineItem] = []
    seen: set[tuple[str, float, str]] = set()

    for index, line in enumerate(normalized_lines):
        maintenance_marker = str(config.get("maintenance_marker", ""))
        if maintenance_marker not in line:
            continue
        amounts = _amounts_near(lines, index, after=_config_int(config, "maintenance_search_after", 25))
        pair = _find_bpi_stamp_duty_pair(
            amounts,
            rate=_config_float(config, "stamp_duty_rate", 0.04),
            max_tax=_config_float(config, "max_stamp_duty", 1.0),
        )
        if pair is None:
            continue
        base, gross = pair
        _append_line_item(
            line_items,
            seen,
            category=str(config.get("maintenance_category", "bank-fee-maintenance")),
            amount=base,
            label=lines[index],
        )
        _append_line_item(
            line_items,
            seen,
            category=str(config.get("stamp_duty_category", "bank-fee-stamp-duty")),
            amount=gross - base,
            label=f"Stamp duty for {lines[index]}",
        )

    for index, line in enumerate(normalized_lines):
        custody_marker = str(config.get("custody_marker", ""))
        if custody_marker not in line:
            continue
        custody_matched = False
        total_marker = str(config.get("total_debit_marker", ""))
        total_search_after = _config_int(config, "custody_total_search_after", 20)
        for offset, candidate_line in enumerate(normalized_lines[index : index + total_search_after]):
            if candidate_line != total_marker:
                continue
            amount = _amounts_near(lines, index + offset, after=_config_int(config, "custody_total_amount_after", 3))
            if amount:
                _append_line_item(
                    line_items,
                    seen,
                    category=str(config.get("custody_category", "bank-custody-fee")),
                    amount=amount[0],
                    label=lines[index],
                )
                custody_matched = True
                break
        if custody_matched:
            continue
        max_amount = _config_float(config, "custody_fallback_max_amount", 100)
        amounts_after = [
            amount
            for amount in _amounts_near(lines, index, after=_config_int(config, "custody_fallback_after", 15))
            if 0 < amount <= max_amount
        ]
        if index > 0 and maintenance_marker in normalized_lines[index - 1]:
            if len(amounts_after) >= 2:
                _append_line_item(
                    line_items,
                    seen,
                    category=str(config.get("custody_category", "bank-custody-fee")),
                    amount=amounts_after[1],
                    label=lines[index],
                )
                continue
        amounts_before = [
            amount
            for amount in _amounts_near(lines, index, before=_config_int(config, "custody_fallback_before", 15))
            if 0 < amount <= max_amount
        ]
        if amounts_before:
            _append_line_item(
                line_items,
                seen,
                category=str(config.get("custody_category", "bank-custody-fee")),
                amount=amounts_before[-1],
                label=lines[index],
            )

    if line_items:
        logger.debug(
            f"[LINE-ITEMS] {pdf_path.name}: "
            f"{', '.join(f'{item.category}={item.amount:.2f}' for item in line_items)}"
        )
    return line_items


def _parse_page_issue_date(page_text: str, fallback_date: Optional[str]) -> Optional[date]:
    match = _DATE_DMY_RE.search(page_text)
    if match:
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            pass
    return _date_from_iso(fallback_date)


def _parse_partial_movement_date(value: str, issue_date: Optional[date]) -> Optional[str]:
    match = _DATE_DM_RE.fullmatch(value.strip())
    if not match or issue_date is None:
        return None

    day, month = (int(part) for part in match.groups())
    year = issue_date.year
    if issue_date.month == 1 and month == 12:
        year -= 1
    elif issue_date.month == 12 and month == 1:
        year += 1

    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_partial_movement_date_from_line(value: str, issue_date: Optional[date]) -> Optional[str]:
    if issue_date is None:
        return None
    matches = list(_DATE_DM_RE.finditer(value.strip()))
    for match in reversed(matches):
        parsed = _parse_partial_movement_date(match.group(0), issue_date)
        if parsed:
            return parsed
    return None


def _parse_bpi_stock_settlement_date(lines: list[str], sale_index: int) -> Optional[str]:
    config = _line_item_config("bpi_stock_invoice")
    context = " ".join(lines[sale_index : sale_index + 5])
    match = _DATE_DMY_RE.search(context)
    if not match:
        return None

    day, month, year = (int(part) for part in match.groups())
    try:
        session_date = date(year, month, day)
    except ValueError:
        return None
    return (session_date + timedelta(days=_config_int(config, "settlement_offset_days", 1))).isoformat()


def _extract_bpi_stock_invoice_line_items(pdf_path: Path, data: dict) -> list[CandidateLineItem]:
    config = _line_item_config("bpi_stock_invoice")
    doc_type = (data.get("document_type") or "").lower()
    issuing_party = _normalize_for_match(data.get("issuing_party") or "")
    title = _normalize_for_match(data.get("document_title") or "")
    if not _doc_matches_line_item_config(doc_type, issuing_party, config):
        return []
    title_terms = tuple(_normalize_for_match(item) for item in _config_sequence(config, "title_terms"))
    if title_terms and not any(term in title for term in title_terms):
        return []

    try:
        with fitz.open(pdf_path) as pdf:
            page_lines = [
                [line.strip() for line in page.get_text().splitlines() if line.strip()]
                for page in pdf
            ]
            page_texts = ["\n".join(lines) for lines in page_lines]
    except Exception as exc:
        raise LineItemExtractionError(f"Failed to read {pdf_path.name}: {exc}") from exc

    line_items: list[CandidateLineItem] = []
    seen: set[tuple[str, float, str]] = set()

    for lines, page_text in zip(page_lines, page_texts):
        issue_date = _parse_page_issue_date(page_text, data.get("date_issued"))
        normalized_lines = [strip_diacritics(line).upper() for line in lines]
        sale_entries: list[tuple[int, str, Optional[str], Optional[str]]] = []

        for sale_index, normalized_line in enumerate(normalized_lines):
            required_terms = _config_sequence(config, "sale_required_terms")
            if not all(term in normalized_line for term in required_terms):
                continue
            reference = None
            reference_pattern = _config_regex(config, "order_reference_pattern")
            reference_search_after = _config_int(config, "reference_search_after", 15)
            for candidate_line in lines[sale_index + 1 : sale_index + reference_search_after]:
                match = (
                    reference_pattern.search(strip_diacritics(candidate_line).upper())
                    if reference_pattern
                    else None
                )
                if match:
                    reference = match.group(1)
                    break
            settlement_date = _parse_bpi_stock_settlement_date(lines, sale_index)
            sale_entries.append((sale_index, lines[sale_index], reference, settlement_date))

        if not sale_entries:
            continue

        first_sale_index = sale_entries[0][0]
        total_credit_indices = [
            index
            for index, normalized_line in enumerate(normalized_lines[:first_sale_index])
            if normalized_line == str(config.get("total_credit_marker", "TOTAL A CREDITO"))
        ]
        if not total_credit_indices:
            continue

        amount_entries = _parse_currency_amounts_between(
            lines,
            total_credit_indices[0] + 1,
            first_sale_index,
        )
        sale_count = min(len(sale_entries), len(total_credit_indices))
        if len(amount_entries) >= 2 * sale_count:
            credit_entries = amount_entries[1 : 2 * sale_count : 2]
        elif len(amount_entries) >= sale_count:
            credit_entries = amount_entries[-sale_count:]
        else:
            continue

        for (sale_index, sale_line, reference, settlement_date), (amount, currency) in zip(
            sale_entries,
            credit_entries,
        ):
            movement_date = None
            lookback = _config_int(config, "movement_date_lookback", 25)
            for date_line in reversed(lines[max(0, sale_index - lookback) : sale_index + 1]):
                movement_date = _parse_partial_movement_date_from_line(date_line, issue_date)
                if movement_date:
                    break
            movement_date = settlement_date or movement_date

            label = sale_line if reference is None else f"{sale_line} ({reference})"
            _append_line_item(
                line_items,
                seen,
                category=str(config.get("category", "stock-sale-bpi")),
                amount=amount,
                label=label,
                date_issued=movement_date,
                reference=reference,
                amount_currency=currency,
                amount_match_required=False,
                document_type=str(config.get("document_type", "bank-stock-sell")),
            )

    if line_items:
        summary = ", ".join(
            f"{item.category}={item.amount:.2f} {item.amount_currency or ''}@{item.date_issued}"
            for item in line_items
        )
        logger.debug(
            f"[LINE-ITEMS] {pdf_path.name}: "
            f"{summary}"
        )
    return line_items


def _extract_bpi_transfer_line_items(pdf_path: Path, data: dict) -> list[CandidateLineItem]:
    config = _line_item_config("bpi_transfer")
    doc_type = (data.get("document_type") or "").lower()
    issuing_party = _normalize_for_match(data.get("issuing_party") or "")
    if not _doc_matches_line_item_config(doc_type, issuing_party, config):
        return []
    line_pattern = _config_regex(config, "line_pattern", flags=re.IGNORECASE)
    if line_pattern is None:
        return []

    try:
        with fitz.open(pdf_path) as pdf:
            page_lines = [
                [line.strip() for line in page.get_text().splitlines() if line.strip()]
                for page in pdf
            ]
            page_texts = ["\n".join(lines) for lines in page_lines]
    except Exception as exc:
        raise LineItemExtractionError(f"Failed to read {pdf_path.name}: {exc}") from exc

    line_items: list[CandidateLineItem] = []
    seen: set[tuple[str, float, str]] = set()

    for lines, page_text in zip(page_lines, page_texts):
        issue_date = _parse_page_issue_date(page_text, data.get("date_issued"))
        normalized_lines = [strip_diacritics(line).upper() for line in lines]
        for index, normalized_line in enumerate(normalized_lines):
            match = line_pattern.search(normalized_line)
            if not match:
                continue

            amounts = _amounts_near(lines, index, before=_config_int(config, "amount_search_before", 8))
            if not amounts:
                continue

            movement_date = None
            date_search_after = _config_int(config, "date_search_after", 5)
            for line in lines[index + 1 : index + date_search_after]:
                movement_date = _parse_partial_movement_date(line, issue_date)
                if movement_date:
                    break

            reference = match.group(1)
            _append_line_item(
                line_items,
                seen,
                category=str(config.get("category", "bank-transfer-sepa")),
                amount=amounts[-1],
                label=lines[index],
                date_issued=movement_date,
                reference=reference,
            )

    if line_items:
        logger.debug(
            f"[LINE-ITEMS] {pdf_path.name}: "
            f"{', '.join(f'{item.label}={item.amount:.2f}@{item.date_issued}' for item in line_items)}"
        )
    return line_items


def _extract_millennium_fee_invoice_line_items(pdf_path: Path, data: dict) -> list[CandidateLineItem]:
    config = _line_item_config("millennium_fee_invoice")
    doc_type = (data.get("document_type") or "").lower()
    issuing_party = _normalize_for_match(data.get("issuing_party") or "")
    if not _doc_matches_line_item_config(doc_type, issuing_party, config):
        return []

    try:
        with fitz.open(pdf_path) as pdf:
            lines = [line.strip() for page in pdf for line in page.get_text().splitlines() if line.strip()]
    except Exception as exc:
        raise LineItemExtractionError(f"Failed to read {pdf_path.name}: {exc}") from exc

    normalized_lines = [strip_diacritics(line).upper() for line in lines]
    movement_date = _extract_millennium_movement_date(lines, normalized_lines) or data.get("date_issued")
    line_items: list[CandidateLineItem] = []
    seen: set[tuple[str, float, str]] = set()

    for index, normalized_line in enumerate(normalized_lines):
        amount = _next_amount_line(lines, index, after=_config_int(config, "amount_search_after", 5))
        if amount is None:
            continue

        matched_category = None
        for marker, category in dict(config.get("markers", {})).items():
            if str(marker) in normalized_line:
                matched_category = str(category)
                break
        if matched_category:
            _append_line_item(
                line_items,
                seen,
                category=matched_category,
                amount=amount,
                label=lines[index],
                date_issued=movement_date,
            )
        elif (
            any(marker in normalized_line for marker in _config_sequence(config, "stamp_duty_markers"))
            and str(config.get("stamp_duty_legal_reference", "")) in normalized_line
        ):
            _append_line_item(
                line_items,
                seen,
                category=str(config.get("stamp_duty_category", "bank-fee-stamp-duty")),
                amount=amount,
                label=lines[index],
                date_issued=movement_date,
            )

    if line_items:
        logger.debug(
            f"[LINE-ITEMS] {pdf_path.name}: "
            f"{', '.join(f'{item.category}={item.amount:.2f}@{item.date_issued}' for item in line_items)}"
        )
    return line_items


def _extract_millennium_movement_date(lines: list[str], normalized_lines: list[str]) -> Optional[str]:
    config = _line_item_config("millennium_fee_invoice")
    marker = str(config.get("movement_date_marker", "DATA DO MOVIMENTO"))
    amount_search_after = _config_int(config, "amount_search_after", 5)
    for index, normalized_line in enumerate(normalized_lines):
        if marker not in normalized_line:
            continue
        for candidate_line in lines[index + 1 : index + amount_search_after]:
            parsed = _date_from_iso(candidate_line.strip())
            if parsed:
                return parsed.isoformat()
    return None


def _next_amount_line(lines: list[str], index: int, *, after: int = 5) -> Optional[float]:
    for line in lines[index + 1 : index + after]:
        amount = _parse_euro_amount_line(line)
        if amount is not None:
            return amount
    return None


def _extract_direct_debit_invoice_line_items(pdf_path: Path, data: dict) -> list[CandidateLineItem]:
    config = _line_item_config("direct_debit")
    doc_type = (data.get("document_type") or "").lower()
    if doc_type not in _reconciliation_policy().supporting_doc_type_patterns:
        return []
    date_pattern = _config_regex(config, "date_pattern")
    amount_pattern = _config_regex(config, "amount_pattern")
    auth_pattern = _config_regex(config, "auth_pattern")
    if date_pattern is None or amount_pattern is None:
        return []

    try:
        with fitz.open(pdf_path) as pdf:
            text = "\n".join(page.get_text() for page in pdf)
    except Exception as exc:
        raise LineItemExtractionError(f"Failed to read {pdf_path.name}: {exc}") from exc

    normalized_text = strip_diacritics(text).upper()
    date_match = date_pattern.search(normalized_text)
    amount_match = amount_pattern.search(normalized_text)
    if not date_match or not amount_match:
        return []

    day, month, year = (int(part) for part in date_match.groups())
    try:
        debit_date = date(year, month, day).isoformat()
    except ValueError:
        return []

    try:
        amount = float(amount_match.group(1).replace(" ", "").replace(".", "").replace(",", "."))
    except ValueError:
        return []

    auth_match = auth_pattern.search(normalized_text) if auth_pattern else None
    reference = auth_match.group(1) if auth_match else None

    line_items: list[CandidateLineItem] = []
    seen: set[tuple[str, float, str]] = set()
    label_prefix = str(config.get("label", "Direct debit"))
    label = label_prefix if reference is None else f"{label_prefix} {reference}"
    _append_line_item(
        line_items,
        seen,
        category=str(config.get("category", "supplier-payment")),
        amount=amount,
        label=label,
        date_issued=debit_date,
        reference=reference,
        amount_currency=data.get("total_amount_currency") or _default_currency(),
        document_type=data.get("document_type"),
    )
    return line_items


def _extract_insurance_notice_line_items(pdf_path: Path, data: dict) -> list[CandidateLineItem]:
    config = _line_item_config("insurance_notice")
    doc_type = (data.get("document_type") or "").lower()
    doc_types = {item.lower() for item in _config_sequence(config, "document_types")}
    if doc_types and doc_type not in doc_types:
        return []
    period_pattern = _config_regex(config, "period_pattern", flags=re.DOTALL)
    reference_pattern = _config_regex(config, "reference_pattern")
    if period_pattern is None:
        return []

    amount = _coerce_amount(data.get("total_amount"))
    if amount is None:
        return []

    try:
        with fitz.open(pdf_path) as pdf:
            text = "\n".join(page.get_text() for page in pdf)
    except Exception as exc:
        raise LineItemExtractionError(f"Failed to read {pdf_path.name}: {exc}") from exc

    normalized_text = strip_diacritics(text).upper()
    period_match = period_pattern.search(normalized_text)
    if not period_match:
        return []

    day, month, year = (int(part) for part in period_match.groups())
    try:
        debit_date = date(year, month, day).isoformat()
    except ValueError:
        return []

    adc_match = reference_pattern.search(normalized_text) if reference_pattern else None
    reference = adc_match.group(1) if adc_match else None
    label_prefix = str(config.get("label", "Insurance direct debit"))
    label = label_prefix if reference is None else f"{label_prefix} {reference}"

    line_items: list[CandidateLineItem] = []
    seen: set[tuple[str, float, str]] = set()
    _append_line_item(
        line_items,
        seen,
        category=str(config.get("category", "supplier-payment")),
        amount=amount,
        label=label,
        date_issued=debit_date,
        reference=reference,
        amount_currency=data.get("total_amount_currency") or _default_currency(),
        document_type=data.get("document_type"),
    )
    return line_items


def _extract_reconciliation_line_items(pdf_path: Path, data: dict) -> list[CandidateLineItem]:
    return [
        *_extract_bpi_fee_invoice_line_items(pdf_path, data),
        *_extract_bpi_stock_invoice_line_items(pdf_path, data),
        *_extract_bpi_transfer_line_items(pdf_path, data),
        *_extract_millennium_fee_invoice_line_items(pdf_path, data),
        *_extract_direct_debit_invoice_line_items(pdf_path, data),
        *_extract_insurance_notice_line_items(pdf_path, data),
    ]


def _candidate_matches_relevant_amounts(candidate: PDFCandidate, relevant_amounts: set[float]) -> bool:
    amounts = []
    if candidate.total_amount is not None:
        amounts.append(candidate.total_amount)
    amounts.extend(item.amount for item in candidate.line_items)
    return any(
        abs(candidate_amount - amount) <= _amount_tolerance()
        for candidate_amount in amounts
        for amount in relevant_amounts
    )


def _candidate_matches_shared_requirements(
    candidate: PDFCandidate,
    shared_requirements: list[tuple[str, Optional[str]]],
) -> bool:
    doc_type = candidate.effective_document_type or candidate.document_type
    if not doc_type:
        return False

    engine = _rule_engine()
    for type_pattern, issuing_party_filter in shared_requirements:
        if not engine.match_doc_type(doc_type, type_pattern):
            continue
        if issuing_party_filter is None:
            return True
        if _normalize_for_match(candidate.issuing_party or "") == _normalize_for_match(issuing_party_filter):
            return True
    return False


def _is_relevant_supplemental_candidate(
    candidate: PDFCandidate,
    *,
    min_txn_date: Optional[date],
    max_txn_date: Optional[date],
    relevant_amounts: set[float],
    shared_requirements: list[tuple[str, Optional[str]]],
    required_patterns: list[str],
) -> bool:
    candidate_date = _date_from_iso(candidate.date_issued)
    if candidate_date is None or min_txn_date is None or max_txn_date is None:
        return False

    window_start = min_txn_date - timedelta(days=_date_window_days())
    window_end = max_txn_date + timedelta(days=_date_window_days())
    if candidate_date < window_start or candidate_date > window_end:
        return False

    if not (
        _candidate_matches_relevant_amounts(candidate, relevant_amounts)
        or _candidate_matches_shared_requirements(candidate, shared_requirements)
    ):
        return False

    if not required_patterns:
        return True

    return _candidate_matches_patterns(candidate, required_patterns)


def _latest_reconciliation_input_mtime(
    repository: DocumentRepository,
    export_path: Path,
) -> float:
    latest_mtime = 0.0
    for search_path in _reconciliation_search_paths(export_path):
        for json_path, data in repository.iter_sidecars(search_path):
            latest_mtime = max(latest_mtime, json_path.stat().st_mtime)
            doc_path = repository.find_companion(json_path, data)
            if doc_path and doc_path.exists():
                latest_mtime = max(latest_mtime, doc_path.stat().st_mtime)
    return latest_mtime


def discover_statements_requiring_reconciliation(
    repository: DocumentRepository,
    export_path: Path,
    *,
    include_stale: bool = True,
) -> list[Path]:
    latest_input_mtime = _latest_reconciliation_input_mtime(repository, export_path)
    pending = []
    for statement_path in discover_bank_statements(repository, export_path):
        reconciliation_path = statement_path.with_suffix(".reconciliation.json")
        if not reconciliation_path.exists():
            pending.append(statement_path)
            continue
        if include_stale and reconciliation_path.stat().st_mtime < latest_input_mtime:
            pending.append(statement_path)
    return pending


def _load_pdf_candidates(
    repository: DocumentRepository,
    export_path: Path,
    *,
    exclude_prefixes: list[str] | None = None,
) -> list[PDFCandidate]:
    exclude_prefixes = exclude_prefixes or []
    policy = _reconciliation_policy()
    candidates: list[PDFCandidate] = []
    seen_candidate_ids: set[str] = set()
    for json_path, data in repository.iter_sidecars(export_path):
        doc_path = repository.find_companion(json_path, data)
        if doc_path is None:
            continue
        if data.get("document_type") == "bank-statement" and doc_path.suffix.lower() == ".xlsx":
            continue

        excluded = any(doc_path.name.startswith(prefix) for prefix in exclude_prefixes)
        if excluded:
            logger.debug(f"[EXCLUDE-PREFIX] Skipping {doc_path.name} from matching")

        sub_docs = data.get("sub_documents")
        if sub_docs and len(sub_docs) >= 2:
            for index, sub_doc in enumerate(sub_docs):
                candidates.append(
                    _build_candidate(
                        json_path,
                        doc_path.name,
                        sub_doc,
                        document_title=None,
                        file_extension=doc_path.suffix.lower(),
                        sub_doc_index=index,
                        is_sub_document=True,
                        exclude_from_matching=excluded,
                        policy=policy,
                    )
                )
                candidate = candidates[-1]
                if candidate.candidate_id in seen_candidate_ids:
                    candidates.pop()
                else:
                    seen_candidate_ids.add(candidate.candidate_id)

        candidate = _build_candidate(
            json_path,
            doc_path.name,
            data,
            document_title=data.get("document_title"),
            page_count=data.get("page_count"),
            file_extension=doc_path.suffix.lower(),
            exclude_from_matching=excluded,
            line_items=_extract_reconciliation_line_items(doc_path, data),
            policy=policy,
        )
        if candidate.candidate_id in seen_candidate_ids:
            continue
        seen_candidate_ids.add(candidate.candidate_id)
        candidates.append(candidate)
    return candidates


def _load_reconciliation_candidates(
    repository: DocumentRepository,
    export_path: Path,
    transactions: list[Transaction],
    *,
    exclude_prefixes: list[str] | None = None,
    rules: list | None = None,
) -> list[PDFCandidate]:
    search_paths = _reconciliation_search_paths(export_path)
    prior_reconciled_keys = _load_reconciled_candidate_keys(
        _prior_reconciliation_paths(export_path)
    )

    if len(search_paths) == 1:
        path_candidates = _load_pdf_candidates(
            repository,
            export_path,
            exclude_prefixes=exclude_prefixes,
        )
        return [
            candidate
            for candidate in path_candidates
            if not _is_prior_reconciled_candidate(candidate, prior_reconciled_keys)
        ]

    rules = rules or []
    min_txn_date, max_txn_date = _transaction_date_bounds(transactions)
    relevant_amounts = {abs(txn.amount) for txn in transactions}
    relevant_amounts.update(_relevant_companion_amounts(transactions, rules))
    shared_requirements = _shared_requirements(transactions, rules)
    required_patterns = _required_patterns(transactions, rules)

    candidates: list[PDFCandidate] = []
    seen_candidate_ids: set[str] = set()
    primary_path = export_path.resolve()
    for search_path in search_paths:
        is_primary_path = search_path.resolve() == primary_path
        path_candidates = _load_pdf_candidates(
            repository,
            search_path,
            exclude_prefixes=exclude_prefixes,
        )
        for candidate in path_candidates:
            if candidate.candidate_id in seen_candidate_ids:
                continue
            if _is_prior_reconciled_candidate(candidate, prior_reconciled_keys):
                logger.debug(f"[PRIOR-RECONCILED] Skipping {candidate.pdf_filename}")
                continue
            if not is_primary_path and not _is_relevant_supplemental_candidate(
                candidate,
                min_txn_date=min_txn_date,
                max_txn_date=max_txn_date,
                relevant_amounts=relevant_amounts,
                shared_requirements=shared_requirements,
                required_patterns=required_patterns,
            ):
                continue
            seen_candidate_ids.add(candidate.candidate_id)
            candidates.append(candidate)
    return candidates


def _candidate_belongs_to_export_path(candidate: PDFCandidate, export_path: Path) -> bool:
    try:
        candidate.json_path.resolve().relative_to(export_path.resolve())
        return True
    except ValueError:
        return False


def _candidate_matches_export_month(candidate: PDFCandidate, export_path: Path) -> bool:
    export_key = _export_month_key(export_path)
    if export_key is None or not candidate.date_issued:
        return True

    candidate_date = _date_from_iso(candidate.date_issued)
    if candidate_date is None:
        return True
    return (candidate_date.year, candidate_date.month) == export_key


def _days_between(date_str1: Optional[str], date_str2: Optional[str]) -> Optional[int]:
    if not date_str1 or not date_str2:
        return None
    try:
        left = datetime.strptime(date_str1, "%Y-%m-%d")
        right = datetime.strptime(date_str2, "%Y-%m-%d")
        return abs((left - right).days)
    except ValueError:
        return None


def _signed_days_between(date_str1: Optional[str], date_str2: Optional[str]) -> Optional[int]:
    if not date_str1 or not date_str2:
        return None
    try:
        left = datetime.strptime(date_str1, "%Y-%m-%d")
        right = datetime.strptime(date_str2, "%Y-%m-%d")
        return (right - left).days
    except ValueError:
        return None


def _candidate_signature(cand: PDFCandidate) -> tuple[str, str]:
    doc_type = (cand.effective_document_type or cand.document_type or "$unknown$").lower()
    issuing_party = _normalize_for_match(cand.issuing_party or "$unknown$")
    return doc_type, issuing_party


def _candidate_date_rank(txn_date: Optional[str], cand: PDFCandidate) -> Optional[tuple[int, bool, int]]:
    days = _days_between(txn_date, cand.date_issued)
    if days is None or days > _date_window_days():
        return None
    signed_days = _signed_days_between(txn_date, cand.date_issued) or 0
    return (days, signed_days > 0, abs(signed_days))


def _candidate_rank_for_transaction(
    txn: Transaction,
    candidate: PDFCandidate,
    category: str,
) -> Optional[tuple[int, bool, int]]:
    txn_date = txn.date_posting or txn.date_value
    relevant_line_items = [
        item
        for item in candidate.line_items
        if _line_item_category_matches(category, item)
        and (
            not item.amount_match_required
            or abs(abs(txn.amount) - item.amount) <= _amount_tolerance()
        )
    ]
    if relevant_line_items:
        ranks = [
            rank
            for item in relevant_line_items
            if (rank := _line_item_date_rank(txn_date, candidate, item)) is not None
        ]
        return min(ranks) if ranks else None
    return _candidate_date_rank(txn_date, candidate)


def _line_item_date_rank(
    txn_date: Optional[str],
    candidate: PDFCandidate,
    line_item: CandidateLineItem,
) -> Optional[tuple[int, bool, int]]:
    line_item_date = line_item.date_issued or candidate.date_issued
    days = _days_between(txn_date, line_item_date)
    if days is None or days > _date_window_days():
        return None
    signed_days = _signed_days_between(txn_date, line_item_date) or 0
    return (days, signed_days > 0, abs(signed_days))


def _same_calendar_month(date_str1: Optional[str], date_str2: Optional[str]) -> bool:
    if not date_str1 or not date_str2:
        return False
    try:
        left = datetime.strptime(date_str1, "%Y-%m-%d")
        right = datetime.strptime(date_str2, "%Y-%m-%d")
    except ValueError:
        return False
    return (left.year, left.month) == (right.year, right.month)


def _is_via_verde_transaction(txn: Transaction) -> bool:
    normalized = _normalize_for_match(txn.description).upper()
    return any(
        (normalized_keyword := _normalize_for_match(keyword).upper())
        and normalized_keyword in normalized
        for keywords in _reconciliation_policy().shared_period_transaction_keywords.values()
        for keyword in keywords
    )


def _is_via_verde_shared_period_candidate(candidate: PDFCandidate) -> bool:
    return candidate.is_shared_period_document and _is_shared_period_counterparty(candidate)


def _is_shared_period_counterparty(candidate: PDFCandidate) -> bool:
    shared_parties = set(_reconciliation_policy().shared_period_transaction_keywords)
    return candidate.counterparty_id in shared_parties


def _is_bank_generated_candidate(candidate: PDFCandidate) -> bool:
    doc_type = candidate.effective_document_type or candidate.document_type
    return bool(doc_type and doc_type.lower() in _reconciliation_policy().bank_generated_doc_types)


def _is_bank_export_candidate(candidate: PDFCandidate) -> bool:
    return candidate.pdf_filename.startswith(_reconciliation_policy().bank_export_prefix)


def _is_supporting_export_candidate(candidate: PDFCandidate) -> bool:
    policy = _reconciliation_policy()
    if not candidate.pdf_filename.startswith(policy.supporting_export_prefixes):
        return False
    return _candidate_matches_patterns(candidate, list(policy.supporting_doc_type_patterns))


def _candidate_parties_match(left: PDFCandidate, right: PDFCandidate) -> bool:
    left_party = _normalize_for_match(left.issuing_party or "")
    right_party = _normalize_for_match(right.issuing_party or "")
    return bool(left_party and right_party and left_party == right_party)


def _candidate_sort_name(candidate: PDFCandidate) -> str:
    return candidate.source_filename or candidate.pdf_filename


def _is_paired_supporting_candidate(match: MatchResult, candidate: PDFCandidate) -> bool:
    if not _is_supporting_export_candidate(candidate):
        return False
    if candidate.total_amount is None:
        return False
    if abs(abs(match.transaction.amount) - candidate.total_amount) > _amount_tolerance():
        return False
    txn_date = match.transaction.date_posting or match.transaction.date_value
    if _candidate_date_rank(txn_date, candidate) is None:
        return False
    return any(
        _is_bank_export_candidate(anchor) and _candidate_parties_match(candidate, anchor)
        for anchor in match.pdf_candidates
    )


def _has_transfer_line_items(candidate: PDFCandidate) -> bool:
    return any(item.category.startswith("bank-transfer") for item in candidate.line_items)


def _transfer_line_item_count(candidate: PDFCandidate) -> int:
    return sum(1 for item in candidate.line_items if item.category.startswith("bank-transfer"))


def _is_statement_bank_scoped_candidate(candidate: PDFCandidate) -> bool:
    doc_type = candidate.effective_document_type or candidate.document_type
    return bool(
        doc_type
        and doc_type.lower() in _reconciliation_policy().statement_bank_scoped_doc_types
    )


def _statement_bank_key(issuing_party: Optional[str]) -> Optional[str]:
    if not issuing_party or issuing_party == "$UNKNOWN$":
        return None
    return _reconciliation_policy().statement_bank_issuer_aliases.get(
        _normalize_for_match(issuing_party)
    )


def _is_candidate_compatible_with_statement_bank(
    candidate: PDFCandidate,
    statement_issuing_party: Optional[str],
) -> bool:
    if not statement_issuing_party or not _is_statement_bank_scoped_candidate(candidate):
        return True

    statement_bank = _statement_bank_key(statement_issuing_party)
    if not statement_bank:
        return True

    candidate_bank = _statement_bank_key(candidate.issuing_party)
    if statement_bank in set(_reconciliation_policy().strict_statement_banks):
        return candidate_bank == statement_bank
    if not candidate_bank:
        return True
    return candidate_bank == statement_bank


def _rule_allowed_patterns(rule) -> list[str]:
    return list(rule.required_types.keys()) + list(rule.shared_types.keys())


def _is_same_month_shared_candidate(rule, txn_date: Optional[str], candidate: PDFCandidate) -> bool:
    if rule.name not in _reconciliation_policy().same_month_shared_rule_names:
        return True
    return _same_calendar_month(txn_date, candidate.date_issued)


def _candidate_matches_shared_filters(rule, type_pattern: str, candidate: PDFCandidate) -> bool:
    filters = getattr(rule, "shared_filters", {}).get(type_pattern, {})
    if not filters:
        return True

    engine = _rule_engine()
    for field_name, expected in filters.items():
        if not engine.match_value(getattr(candidate, field_name, None), expected):
            return False
    return True


def _prune_rule_aware_exact_candidates(
    txn: Transaction,
    candidates: list[PDFCandidate],
    rule,
) -> list[PDFCandidate]:
    if not candidates:
        return candidates

    engine = _rule_engine()
    txn_date = txn.date_posting or txn.date_value
    allowed_patterns = _rule_allowed_patterns(rule)
    filtered = [
        candidate
        for candidate in candidates
        if _candidate_matches_patterns(candidate, allowed_patterns)
    ]
    if not filtered:
        return []

    keep_ids: set[str] = set()

    for pattern in rule.shared_types.keys():
        for candidate in filtered:
            doc_type = engine.candidate_doc_type(candidate)
            if doc_type and engine.match_doc_type(doc_type, pattern):
                keep_ids.add(candidate.candidate_id)

    for pattern, cardinality in rule.required_types.items():
        matching = []
        for candidate in filtered:
            doc_type = engine.candidate_doc_type(candidate)
            if doc_type and engine.match_doc_type(doc_type, pattern):
                matching.append(candidate)
        if not matching:
            continue

        same_month_exists = any(
            _same_calendar_month(txn_date, candidate.date_issued)
            for candidate in matching
        )
        if same_month_exists:
            matching = [
                candidate
                for candidate in matching
                if not (
                    _is_bank_generated_candidate(candidate)
                    and not _same_calendar_month(txn_date, candidate.date_issued)
                )
            ]

        matching = sorted(
            matching,
            key=lambda candidate: (
                _candidate_rank_for_transaction(txn, candidate, rule.name) or (9999, True, 9999),
                _candidate_sort_name(candidate),
            ),
        )

        _, max_count = engine.parse_cardinality(cardinality)
        if max_count is not None:
            matching = matching[:max_count]
        else:
            bank_generated = [
                candidate for candidate in matching if _is_bank_generated_candidate(candidate)
            ]
            best_by_signature: dict[tuple[str, str], PDFCandidate] = {}
            for candidate in matching:
                if _is_bank_generated_candidate(candidate):
                    continue
                best_by_signature.setdefault(_candidate_signature(candidate), candidate)
            matching = bank_generated + list(best_by_signature.values())

        for candidate in matching:
            keep_ids.add(candidate.candidate_id)

    return [candidate for candidate in filtered if candidate.candidate_id in keep_ids]


def _phase1_deterministic_match(
    transactions: list[Transaction],
    candidates: list[PDFCandidate],
    rules: list,
) -> tuple[list[MatchResult], list[Transaction], list[PDFCandidate]]:
    matches: list[MatchResult] = []
    unmatched: list[Transaction] = []
    used_candidates: set[str] = set()

    for txn in transactions:
        category, rule = _classify_transaction(txn, rules)
        abs_amount = abs(txn.amount)
        amount_matches: list[tuple[PDFCandidate, tuple[int, bool, int]]] = []
        for cand in candidates:
            if cand.candidate_id in used_candidates:
                continue
            if _has_transfer_line_items(cand):
                continue
            if cand.total_amount is None:
                continue
            if abs(abs_amount - cand.total_amount) <= _amount_tolerance():
                rank = _candidate_rank_for_transaction(txn, cand, category)
                if rank is not None:
                    amount_matches.append((cand, rank))

        if not amount_matches:
            unmatched.append(txn)
            continue

        selected_matches = sorted(
            amount_matches,
            key=lambda item: (item[1], _candidate_sort_name(item[0])),
        )
        matched_pdfs = [candidate for candidate, _ in selected_matches]
        if rule is not None:
            matched_pdfs = _prune_rule_aware_exact_candidates(txn, matched_pdfs, rule)
            if not matched_pdfs:
                unmatched.append(txn)
                continue
        else:
            best_by_signature: dict[tuple[str, str], PDFCandidate] = {}
            for candidate in matched_pdfs:
                key = _candidate_signature(candidate)
                current = best_by_signature.get(key)
                candidate_rank = _candidate_rank_for_transaction(txn, candidate, category) or (9999, True, 9999)
                current_rank = (
                    _candidate_rank_for_transaction(txn, current, category) or (9999, True, 9999)
                    if current is not None
                    else None
                )
                if current is None or candidate_rank < current_rank:
                    best_by_signature[key] = candidate
            matched_pdfs = list(best_by_signature.values())
        closest_days = selected_matches[0][1][0]

        for cand in matched_pdfs:
            used_candidates.add(cand.candidate_id)

        reasoning = (
            f"Amount match: {abs_amount:.2f} "
            f"({len(matched_pdfs)} PDF(s), closest date: {closest_days}d)"
        )
        logger.debug(
            f"[PHASE-1] Row {txn.row_number}: {txn.description[:50]} -> "
            f"{', '.join(c.pdf_filename for c in matched_pdfs)} ({reasoning})"
        )

        matches.append(
            MatchResult(
                transaction=txn,
                pdf_candidates=matched_pdfs,
                method="exact",
                confidence=1.0,
                reasoning=reasoning,
            )
        )

    remaining = [candidate for candidate in candidates if candidate.candidate_id not in used_candidates]
    return matches, unmatched, remaining


def _link_line_item_documents(
    all_matches: list[MatchResult],
    final_unmatched: list[Transaction],
    all_candidates: list[PDFCandidate],
    rules: list,
) -> tuple[list[MatchResult], list[Transaction], set[str]]:
    line_item_candidate_ids: set[str] = set()
    matched_by_row = {match.transaction.row_number: match for match in all_matches}
    still_unmatched_rows: set[int] = {txn.row_number for txn in final_unmatched}
    newly_matched: list[MatchResult] = []

    for txn in list(final_unmatched) + [match.transaction for match in all_matches]:
        category, rule = _classify_transaction(txn, rules)
        if rule is None:
            continue
        txn_date = txn.date_posting or txn.date_value
        abs_amount = abs(txn.amount)

        best_match: tuple[PDFCandidate, CandidateLineItem, tuple[int, bool, int]] | None = None
        for candidate in all_candidates:
            if not candidate.line_items:
                continue
            if _transfer_line_item_count(candidate) > 1:
                continue
            for line_item in candidate.line_items:
                if not _line_item_category_matches(category, line_item):
                    continue
                if (
                    line_item.amount_match_required
                    and abs(line_item.amount - abs_amount) > _amount_tolerance()
                ):
                    continue
                if not _line_item_matches_transaction_context(txn, line_item):
                    continue
                rank = _line_item_date_rank(txn_date, candidate, line_item)
                if rank is None:
                    continue
                current = (candidate, line_item, rank)
                candidate_key = (
                    rank,
                    candidate.page_count or 9999,
                    candidate.pdf_filename,
                    line_item.label,
                )
                best_key = (
                    best_match[2],
                    best_match[0].page_count or 9999,
                    best_match[0].pdf_filename,
                    best_match[1].label,
                ) if best_match is not None else None
                if best_match is None or candidate_key < best_key:
                    best_match = current

        if best_match is None:
            continue

        candidate, line_item, rank = best_match
        line_item_candidate_ids.add(candidate.candidate_id)
        matched_line_item = MatchedLineItem(candidate=candidate, line_item=line_item)
        if txn.row_number in matched_by_row:
            match = matched_by_row[txn.row_number]
            existing_ids = {cand.candidate_id for cand in match.pdf_candidates}
            if candidate.candidate_id not in existing_ids:
                match.pdf_candidates.append(candidate)
            if matched_line_item not in match.line_items:
                match.line_items.append(matched_line_item)
            if match.method == "exact":
                match.reasoning = f"{match.reasoning}; line item match: {line_item.label} ({line_item.amount:.2f})"
        elif txn.row_number in still_unmatched_rows:
            new_match = MatchResult(
                transaction=txn,
                pdf_candidates=[candidate],
                method="line-item",
                confidence=1.0,
                reasoning=(
                    f"Line item match: {line_item.label} "
                    f"{line_item.amount:.2f} ({rank[0]}d from line item date)"
                ),
                line_items=[matched_line_item],
            )
            newly_matched.append(new_match)
            matched_by_row[txn.row_number] = new_match
            still_unmatched_rows.discard(txn.row_number)
            logger.debug(
                f"[LINE-ITEM-MATCH] Row {txn.row_number}: {txn.description[:50]} -> "
                f"{candidate.pdf_filename} ({line_item.label}={line_item.amount:.2f})"
            )

    updated_matches = all_matches + newly_matched
    updated_unmatched = [txn for txn in final_unmatched if txn.row_number in still_unmatched_rows]
    return updated_matches, updated_unmatched, line_item_candidate_ids


def _line_item_category_matches(category: str, line_item: CandidateLineItem) -> bool:
    if line_item.category == category:
        return True
    aliases = _reconciliation_policy().line_item_category_aliases.get(category, {})
    if line_item.category in set(_config_sequence(aliases, "categories")):
        return True
    return any(
        line_item.category.startswith(prefix)
        for prefix in _config_sequence(aliases, "prefixes")
    )


def _link_related_no_amount_documents(
    all_matches: list[MatchResult],
    all_candidates: list[PDFCandidate],
    rules: list,
) -> set[str]:
    related_candidate_ids: set[str] = set()
    claimed_candidate_ids = {
        candidate.candidate_id
        for match in all_matches
        for candidate in match.pdf_candidates
    }
    engine = _rule_engine()

    for match in all_matches:
        _, rule = _classify_transaction(match.transaction, rules)
        if rule is None:
            continue
        allowed_patterns = _rule_allowed_patterns(rule)
        if not allowed_patterns:
            continue

        txn_date = match.transaction.date_posting or match.transaction.date_value
        existing_ids = {candidate.candidate_id for candidate in match.pdf_candidates}
        related_for_match: list[PDFCandidate] = []

        anchors = [
            candidate
            for candidate in match.pdf_candidates
            if candidate.total_amount is not None
            and _candidate_matches_patterns(candidate, allowed_patterns)
        ]
        for anchor in anchors:
            anchor_doc_type = engine.candidate_doc_type(anchor)
            anchor_party = _normalize_for_match(anchor.issuing_party or "")
            if not anchor_doc_type or not anchor_party or not anchor.date_issued:
                continue

            for candidate in sorted(all_candidates, key=_candidate_sort_name):
                if candidate.candidate_id in existing_ids or candidate.candidate_id in claimed_candidate_ids:
                    continue
                if candidate.total_amount is not None:
                    continue
                if candidate.date_issued != anchor.date_issued:
                    continue
                if _candidate_date_rank(txn_date, candidate) is None:
                    continue
                candidate_doc_type = engine.candidate_doc_type(candidate)
                if candidate_doc_type != anchor_doc_type:
                    continue
                if _normalize_for_match(candidate.issuing_party or "") != anchor_party:
                    continue
                if not _candidate_matches_patterns(candidate, allowed_patterns):
                    continue

                match.pdf_candidates.append(candidate)
                existing_ids.add(candidate.candidate_id)
                claimed_candidate_ids.add(candidate.candidate_id)
                related_candidate_ids.add(candidate.candidate_id)
                related_for_match.append(candidate)

        if related_for_match:
            related_names = ", ".join(candidate.pdf_filename for candidate in related_for_match)
            match.reasoning = f"{match.reasoning}; related no-amount document(s): {related_names}"
            logger.debug(
                f"[RELATED-DOC] Row {match.transaction.row_number}: "
                f"{', '.join(candidate.pdf_filename for candidate in related_for_match)}"
            )

    return related_candidate_ids


def _link_paired_supporting_documents(
    all_matches: list[MatchResult],
    all_candidates: list[PDFCandidate],
    rules: list,
) -> set[str]:
    supporting_candidate_ids: set[str] = set()
    claimed_candidate_ids = {
        candidate.candidate_id
        for match in all_matches
        for candidate in match.pdf_candidates
    }

    for match in all_matches:
        category, _ = _classify_transaction(match.transaction, rules)
        existing_ids = {candidate.candidate_id for candidate in match.pdf_candidates}
        bank_anchors = [
            candidate
            for candidate in match.pdf_candidates
            if _is_bank_export_candidate(candidate)
        ]
        if not bank_anchors:
            continue

        related_for_match: list[PDFCandidate] = []
        for candidate in sorted(all_candidates, key=_candidate_sort_name):
            if candidate.candidate_id in existing_ids or candidate.candidate_id in claimed_candidate_ids:
                continue
            if not _is_supporting_export_candidate(candidate):
                continue
            if candidate.total_amount is None:
                continue
            if abs(abs(match.transaction.amount) - candidate.total_amount) > _amount_tolerance():
                continue
            if _candidate_rank_for_transaction(match.transaction, candidate, category) is None:
                continue
            if not any(_candidate_parties_match(candidate, anchor) for anchor in bank_anchors):
                continue
            candidate_party = _normalize_for_match(candidate.issuing_party or "")
            if any(
                _is_paired_supporting_candidate(match, existing)
                and _normalize_for_match(existing.issuing_party or "") == candidate_party
                for existing in match.pdf_candidates
            ):
                continue

            match.pdf_candidates.append(candidate)
            existing_ids.add(candidate.candidate_id)
            claimed_candidate_ids.add(candidate.candidate_id)
            supporting_candidate_ids.add(candidate.candidate_id)
            related_for_match.append(candidate)

        if related_for_match:
            related_names = ", ".join(candidate.pdf_filename for candidate in related_for_match)
            match.reasoning = f"{match.reasoning}; paired support document(s): {related_names}"
            logger.debug(
                f"[PAIRED-SUPPORT] Row {match.transaction.row_number}: "
                f"{', '.join(candidate.pdf_filename for candidate in related_for_match)}"
            )

    return supporting_candidate_ids


def _link_evidence_counterparty_documents(
    all_matches: list[MatchResult],
    all_candidates: list[PDFCandidate],
    rules: list,
) -> set[str]:
    evidence_candidate_ids: set[str] = set()
    engine = _rule_engine()

    for match in all_matches:
        category, rule = _classify_transaction(match.transaction, rules)
        policy = _reconciliation_policy()
        if rule is None or category not in set(policy.evidence_counterparty_categories):
            continue
        required_pattern = policy.evidence_counterparty_required_pattern
        if not any(engine.match_doc_type(required_pattern, pattern) for pattern in rule.required_types):
            continue
        skip_supplier_present_categories = set(
            policy.evidence_counterparty_skip_if_supplier_present_categories
        )
        if category in skip_supplier_present_categories and any(
            candidate.is_supplier_evidence for candidate in match.pdf_candidates
        ):
            continue

        txn_date = match.transaction.date_posting or match.transaction.date_value
        anchors = [
            candidate
            for candidate in match.pdf_candidates
            if candidate.counterparty_id != "$UNKNOWN$"
            and (candidate.is_bank_anchor or candidate.is_supplier_evidence)
        ]
        if not anchors:
            continue

        existing_ids = {candidate.candidate_id for candidate in match.pdf_candidates}
        related_for_match: list[PDFCandidate] = []
        for anchor in anchors:
            candidates = [
                candidate
                for candidate in all_candidates
                if candidate.candidate_id not in existing_ids
                and candidate.is_supplier_evidence
                and candidate.counterparty_id == anchor.counterparty_id
                and _same_calendar_month(txn_date, candidate.date_issued)
                and (
                    candidate.is_shared_period_document
                    or category in set(policy.evidence_counterparty_amount_optional_categories)
                    or _candidate_amount_matches_transaction(match, candidate)
                )
            ]
            if not candidates:
                continue

            selected = sorted(
                candidates,
                key=lambda candidate: (
                    not candidate.is_shared_period_document,
                    candidate.date_issued != txn_date,
                    _amount_distance(match, candidate),
                    _candidate_date_rank(txn_date, candidate) or (9999, True, 9999),
                    candidate.pdf_filename,
                ),
            )[0]
            match.pdf_candidates.append(selected)
            existing_ids.add(selected.candidate_id)
            evidence_candidate_ids.add(selected.candidate_id)
            related_for_match.append(selected)

        if related_for_match:
            related_names = ", ".join(candidate.pdf_filename for candidate in related_for_match)
            match.reasoning = f"{match.reasoning}; counterparty evidence document(s): {related_names}"
            logger.debug(
                f"[COUNTERPARTY-EVIDENCE] Row {match.transaction.row_number}: {related_names}"
            )

    return evidence_candidate_ids


def _candidate_amount_matches_transaction(match: MatchResult, candidate: PDFCandidate) -> bool:
    if candidate.total_amount is None:
        return False
    return abs(abs(match.transaction.amount) - candidate.total_amount) <= _amount_tolerance()


def _amount_distance(match: MatchResult, candidate: PDFCandidate) -> float:
    if candidate.total_amount is None:
        return float("inf")
    return abs(abs(match.transaction.amount) - candidate.total_amount)


def _extract_period_tokens(text: str) -> set[str]:
    normalized = re.sub(r"[^A-Z0-9]+", " ", strip_diacritics(text).upper())
    return {f"{month} {year}" for month, year in _PERIOD_TOKEN_RE.findall(normalized)}


def _line_item_matches_transaction_context(txn: Transaction, line_item: CandidateLineItem) -> bool:
    if line_item.reference and line_item.category.startswith("bank-transfer"):
        if not re.search(rf"(?<!\d){re.escape(line_item.reference)}(?!\d)", txn.description):
            return False

    if not line_item.amount_match_required and line_item.date_issued:
        txn_date = txn.date_posting or txn.date_value
        if txn_date and txn_date != line_item.date_issued:
            return False

    if line_item.category.startswith("bank-transfer") and line_item.date_issued:
        txn_date = txn.date_posting or txn.date_value
        if txn_date and not _same_calendar_month(txn_date, line_item.date_issued):
            return False

    txn_periods = _extract_period_tokens(txn.description)
    if not txn_periods:
        return True
    line_item_periods = _extract_period_tokens(line_item.label)
    return not line_item_periods or bool(txn_periods & line_item_periods)


def _format_candidate_for_llm(idx: int, cand: PDFCandidate) -> str:
    parts = [cand.pdf_filename]
    if cand.sub_doc_index is not None:
        parts.append(f"(sub-doc #{cand.sub_doc_index})")
    if cand.issuing_party and cand.issuing_party != "$UNKNOWN$":
        parts.append(cand.issuing_party)
    if cand.document_title:
        parts.append(cand.document_title)
    if cand.total_amount is not None:
        currency = cand.total_amount_currency or _default_currency()
        parts.append(f"{cand.total_amount:.2f} {currency}")
    if cand.date_issued and cand.date_issued != "$UNKNOWN$":
        parts.append(cand.date_issued)
    label = chr(ord("A") + idx) if idx < 26 else f"P{idx}"
    return f"[{label}] {' - '.join(parts)}"


def _phase2_llm_match(
    runtime: Runtime,
    transactions: list[Transaction],
    candidates: list[PDFCandidate],
) -> list[MatchResult]:
    if not transactions or not candidates or runtime.openai_client is None:
        return []

    txn_lines = []
    for index, txn in enumerate(transactions, 1):
        txn_date = txn.date_posting or txn.date_value or "unknown"
        txn_lines.append(f'[{index}] {txn_date} | {txn.amount:.2f} {txn.currency} | "{txn.description}"')

    cand_lines = []
    for index, candidate in enumerate(candidates):
        cand_lines.append(_format_candidate_for_llm(index, candidate))

    cand_labels = {}
    for index in range(len(candidates)):
        label = chr(ord("A") + index) if index < 26 else f"P{index}"
        cand_labels[label] = candidates[index]

    date_window_days = _date_window_days()
    prompt = f"""You are a bank reconciliation assistant. Match bank transactions to supporting PDF documents.

UNMATCHED TRANSACTIONS:
{chr(10).join(txn_lines)}

AVAILABLE PDF DOCUMENTS:
{chr(10).join(cand_lines)}

Match transactions to PDFs. Consider:
- Bank descriptions are abbreviated; match to issuing_party and document_title
- Amounts may differ slightly (fees, taxes included)
- Dates may differ by up to {date_window_days} days
- Some transactions may have NO match — do not force matches
- One transaction CAN match multiple PDFs (e.g., bank note + vendor invoice)

Respond in JSON:
{{
    "matches": [
        {{
            "transaction_id": 1,
            "pdf_ids": ["A", "B"],
            "confidence": 0.9,
            "reasoning": "Brief explanation"
        }}
    ],
    "unmatched_transactions": [2, 3]
}}"""

    try:
        response = runtime.openai_client.chat.completions.create(
            model=runtime.model_id,
            max_tokens=runtime.profile.openrouter.requests.reconciliation_max_tokens,
            temperature=runtime.profile.openrouter.requests.reconciliation_temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if not content:
            logger.warning("[PHASE-2] Empty LLM response")
            return []
        result = json.loads(_extract_json_from_response(content))
    except Exception as exc:
        logger.error(f"[PHASE-2] LLM matching failed: {exc}")
        return []

    matches: list[MatchResult] = []
    for match in result.get("matches", []):
        txn_idx = match.get("transaction_id")
        if txn_idx is None or txn_idx < 1 or txn_idx > len(transactions):
            continue

        txn = transactions[txn_idx - 1]
        matched_pdfs = []
        for pdf_id in match.get("pdf_ids", []):
            candidate = cand_labels.get(str(pdf_id).upper())
            if candidate:
                matched_pdfs.append(candidate)
        if not matched_pdfs:
            continue

        abs_amount = abs(txn.amount)
        has_amount_match = any(
            candidate.total_amount is not None
            and abs(abs_amount - candidate.total_amount) <= _amount_tolerance()
            for candidate in matched_pdfs
        )
        if not has_amount_match:
            logger.debug(
                f"[PHASE-2] Row {txn.row_number}: rejected LLM match — "
                f"no PDF has matching amount ({abs_amount:.2f})"
            )
            continue

        confidence = match.get("confidence", _reconciliation_policy().llm_default_confidence)
        reasoning = match.get("reasoning", "")
        logger.debug(
            f"[PHASE-2] Row {txn.row_number}: {txn.description[:50]} -> "
            f"{', '.join(candidate.pdf_filename for candidate in matched_pdfs)} "
            f"(confidence={confidence:.1f}, {reasoning})"
        )
        matches.append(
            MatchResult(
                transaction=txn,
                pdf_candidates=matched_pdfs,
                method="llm",
                confidence=confidence,
                reasoning=reasoning,
            )
        )

    return matches


def _normalize_for_match(text: str) -> str:
    return compact_match_key(text)


def _classify_transaction(txn: Transaction, rules: list) -> tuple[str, object | None]:
    return _rule_engine().classify_transaction(txn, rules)


def _count_documents_for_pattern(match: MatchResult, engine: RuleEngine, pattern: str) -> int:
    count = 0
    for candidate in match.pdf_candidates:
        candidate_doc_type = engine.candidate_doc_type(candidate)
        if candidate_doc_type and engine.match_doc_type(candidate_doc_type, pattern):
            count += 1
            continue
        count += sum(
            1
            for item in match.line_items
            if item.candidate is candidate
            and item.line_item.document_type
            and engine.match_doc_type(item.line_item.document_type, pattern)
        )
    return count


def _is_paired_supporting_cardinality_error(
    match: MatchResult,
    rule,
    error: str,
    engine: RuleEngine,
) -> bool:
    error_match = re.fullmatch(
        r"too many (?P<display>.+) \(expected max (?P<max>\d+), found (?P<found>\d+)\)",
        error,
    )
    if not error_match:
        return False

    display_pattern = error_match.group("display")
    max_count = int(error_match.group("max"))
    found_count = int(error_match.group("found"))

    for pattern in rule.required_types:
        if pattern.replace("|", "/") != display_pattern:
            continue
        paired_support_count = sum(
            1
            for candidate in match.pdf_candidates
            if _is_paired_supporting_candidate(match, candidate)
            and (candidate_doc_type := engine.candidate_doc_type(candidate))
            and engine.match_doc_type(candidate_doc_type, pattern)
        )
        if paired_support_count == 0:
            continue
        actual_count = _count_documents_for_pattern(match, engine, pattern)
        if actual_count == found_count and actual_count - paired_support_count <= max_count:
            return True

    return False


def _validate_required_documents(matches: list[MatchResult], rules: list) -> dict[int, list[str]]:
    engine = _rule_engine()
    errors: dict[int, list[str]] = {}
    for match in matches:
        _, rule = _classify_transaction(match.transaction, rules)
        row_errors = engine.validate_match(match, rules)
        policy = _reconciliation_policy()
        if rule is not None:
            row_errors = [
                error
                for error in row_errors
                if not (
                    error.startswith("missing supplier-evidence")
                    and rule.name in policy.shared_period_supplier_evidence_error_exempt_rule_names
                    and any(
                        candidate.is_bank_anchor and _is_shared_period_counterparty(candidate)
                        for candidate in match.pdf_candidates
                    )
                )
                and not (
                    error.startswith("missing bank-anchor")
                    and rule.name in policy.shared_period_bank_anchor_error_exempt_rule_names
                    and _is_via_verde_transaction(match.transaction)
                    and any(
                        _is_via_verde_shared_period_candidate(candidate)
                        for candidate in match.pdf_candidates
                    )
                )
            ]
        paired_support_filenames = {
            candidate.pdf_filename
            for candidate in match.pdf_candidates
            if _is_paired_supporting_candidate(match, candidate)
        }
        if paired_support_filenames:
            row_errors = [
                error
                for error in row_errors
                if not (
                    error.startswith("unexpected ")
                    and any(f"({filename})" in error for filename in paired_support_filenames)
                )
            ]
        if rule is not None:
            row_errors = [
                error
                for error in row_errors
                if not _is_paired_supporting_cardinality_error(match, rule, error, engine)
            ]
        if row_errors:
            errors[match.transaction.row_number] = row_errors
    return errors


def _validate_supporting_documents_have_bank_pair(
    matches: list[MatchResult],
    statement_issuing_party: Optional[str],
) -> dict[int, list[str]]:
    if _statement_bank_key(statement_issuing_party) in set(
        _reconciliation_policy().supporting_pair_exempt_statement_banks
    ):
        return {}

    errors: dict[int, list[str]] = {}
    for match in matches:
        has_supporting_doc = any(
            candidate.pdf_filename.startswith(_reconciliation_policy().supporting_export_prefixes)
            for candidate in match.pdf_candidates
        )
        has_bank_doc = any(
            candidate.pdf_filename.startswith(_reconciliation_policy().bank_export_prefix)
            for candidate in match.pdf_candidates
        )
        has_via_verde_shared_doc = _is_via_verde_transaction(match.transaction) and any(
            _is_via_verde_shared_period_candidate(candidate)
            for candidate in match.pdf_candidates
        )
        if (
            has_supporting_doc
            and not has_bank_doc
            and not match.line_items
            and not has_via_verde_shared_doc
        ):
            policy = _reconciliation_policy()
            supporting_prefixes = "/".join(
                prefix.rstrip("_") for prefix in policy.supporting_export_prefixes
            )
            bank_prefix = policy.bank_export_prefix.rstrip("_")
            errors[match.transaction.row_number] = [
                f"missing {bank_prefix} document for {supporting_prefixes} support file"
            ]
    return errors


def _merge_validation_errors(*error_sets: dict[int, list[str]]) -> dict[int, list[str]]:
    merged: dict[int, list[str]] = {}
    for error_set in error_sets:
        for row_number, row_errors in error_set.items():
            merged.setdefault(row_number, []).extend(row_errors)
    return merged


def _prune_unexpected_candidates(matches: list[MatchResult], rules: list) -> None:
    engine = _rule_engine()
    for match in matches:
        _, rule = _classify_transaction(match.transaction, rules)
        if rule is None:
            continue

        allowed_patterns = list(rule.required_types.keys()) + list(rule.shared_types.keys())
        if not allowed_patterns:
            continue

        filtered_candidates = []
        seen_candidate_ids: set[str] = set()
        for candidate in match.pdf_candidates:
            doc_type = engine.candidate_doc_type(candidate)
            line_item_doc_types = [
                item.line_item.document_type
                for item in match.line_items
                if item.candidate is candidate and item.line_item.document_type
            ]
            if not doc_type and not line_item_doc_types:
                continue
            if _is_paired_supporting_candidate(match, candidate):
                if candidate.candidate_id in seen_candidate_ids:
                    continue
                seen_candidate_ids.add(candidate.candidate_id)
                filtered_candidates.append(candidate)
                continue
            if not (
                doc_type
                and any(engine.match_doc_type(doc_type, pattern) for pattern in allowed_patterns)
            ) and not any(
                engine.match_doc_type(line_item_doc_type, pattern)
                for line_item_doc_type in line_item_doc_types
                for pattern in allowed_patterns
            ):
                continue
            if candidate.candidate_id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(candidate.candidate_id)
            filtered_candidates.append(candidate)

        if filtered_candidates:
            match.pdf_candidates = filtered_candidates


def _link_shared_documents(
    all_matches: list[MatchResult],
    final_unmatched: list[Transaction],
    all_candidates: list[PDFCandidate],
    rules: list,
) -> tuple[list[MatchResult], list[Transaction], set[str]]:
    shared_candidate_ids: set[str] = set()
    matched_by_row = {match.transaction.row_number: match for match in all_matches}
    all_txns = [match.transaction for match in all_matches] + final_unmatched

    newly_matched: list[MatchResult] = []
    still_unmatched_rows: set[int] = {txn.row_number for txn in final_unmatched}

    for txn in all_txns:
        category, rule = _classify_transaction(txn, rules)
        if rule is None or not rule.shared_types:
            continue

        txn_date = txn.date_posting or txn.date_value
        for type_pattern, issuing_party_filter in rule.shared_types.items():
            best_shared_match: tuple[PDFCandidate, tuple[int, bool, int]] | None = None
            for cand in all_candidates:
                doc_type = cand.effective_document_type
                if not doc_type or not _rule_engine().match_doc_type(doc_type, type_pattern):
                    continue
                if not _is_same_month_shared_candidate(rule, txn_date, cand):
                    continue
                if issuing_party_filter is not None and cand.issuing_party:
                    if _normalize_for_match(cand.issuing_party) != _normalize_for_match(issuing_party_filter):
                        continue
                if not _candidate_matches_shared_filters(rule, type_pattern, cand):
                    continue
                rank = _candidate_date_rank(txn_date, cand)
                if rank is None:
                    continue

                if best_shared_match is None or rank < best_shared_match[1]:
                    best_shared_match = (cand, rank)

            shared_cands = [best_shared_match[0]] if best_shared_match else []

            if not shared_cands:
                continue

            for cand in shared_cands:
                shared_candidate_ids.add(cand.candidate_id)

            if txn.row_number in matched_by_row:
                match = matched_by_row[txn.row_number]
                existing_ids = {cand.candidate_id for cand in match.pdf_candidates}
                for cand in shared_cands:
                    if cand.candidate_id not in existing_ids:
                        match.pdf_candidates.append(cand)
                        existing_ids.add(cand.candidate_id)
            elif txn.row_number in still_unmatched_rows:
                newly_matched.append(
                    MatchResult(
                        transaction=txn,
                        pdf_candidates=list(shared_cands),
                        method="shared",
                        confidence=1.0,
                        reasoning=f"Shared {type_pattern} document(s)",
                    )
                )
                still_unmatched_rows.discard(txn.row_number)
                matched_by_row[txn.row_number] = newly_matched[-1]

    updated_matches = all_matches + newly_matched
    updated_unmatched = [txn for txn in final_unmatched if txn.row_number in still_unmatched_rows]
    return updated_matches, updated_unmatched, shared_candidate_ids


def _link_via_verde_period_documents(
    all_matches: list[MatchResult],
    final_unmatched: list[Transaction],
    all_candidates: list[PDFCandidate],
    rules: list,
) -> tuple[list[MatchResult], list[Transaction], set[str]]:
    shared_candidate_ids: set[str] = set()
    matched_by_row = {match.transaction.row_number: match for match in all_matches}
    all_txns = [match.transaction for match in all_matches] + final_unmatched

    newly_matched: list[MatchResult] = []
    still_unmatched_rows: set[int] = {txn.row_number for txn in final_unmatched}

    for txn in all_txns:
        category, rule = _classify_transaction(txn, rules)
        if (
            rule is None
            or category not in set(_reconciliation_policy().shared_period_link_categories)
            or not _is_via_verde_transaction(txn)
        ):
            continue

        txn_date = txn.date_posting or txn.date_value
        best_shared_match: tuple[PDFCandidate, tuple[int, bool, int]] | None = None
        for candidate in all_candidates:
            if not _is_via_verde_shared_period_candidate(candidate):
                continue
            if not _same_calendar_month(txn_date, candidate.date_issued):
                continue
            rank = _candidate_date_rank(txn_date, candidate)
            if rank is None:
                continue
            if best_shared_match is None or rank < best_shared_match[1]:
                best_shared_match = (candidate, rank)

        if best_shared_match is None:
            continue

        shared_candidate = best_shared_match[0]
        shared_candidate_ids.add(shared_candidate.candidate_id)

        if txn.row_number in matched_by_row:
            match = matched_by_row[txn.row_number]
            existing_ids = {candidate.candidate_id for candidate in match.pdf_candidates}
            existing_filenames = {candidate.pdf_filename for candidate in match.pdf_candidates}
            if (
                shared_candidate.candidate_id not in existing_ids
                and shared_candidate.pdf_filename not in existing_filenames
            ):
                match.pdf_candidates.append(shared_candidate)
                if match.method == "exact":
                    match.reasoning = (
                        f"{match.reasoning}; shared period document: "
                        f"{shared_candidate.pdf_filename}"
                    )
        elif txn.row_number in still_unmatched_rows:
            newly_matched.append(
                MatchResult(
                    transaction=txn,
                    pdf_candidates=[shared_candidate],
                    method="shared",
                    confidence=1.0,
                    reasoning=f"Shared period document: {shared_candidate.pdf_filename}",
                )
            )
            still_unmatched_rows.discard(txn.row_number)
            matched_by_row[txn.row_number] = newly_matched[-1]

    updated_matches = all_matches + newly_matched
    updated_unmatched = [txn for txn in final_unmatched if txn.row_number in still_unmatched_rows]
    return updated_matches, updated_unmatched, shared_candidate_ids


def _link_companion_documents(
    all_matches: list[MatchResult],
    final_unmatched: list[Transaction],
    all_candidates: list[PDFCandidate],
    rules: list,
) -> tuple[list[MatchResult], list[Transaction], set[str]]:
    companion_candidate_ids: set[str] = set()
    rules_with_companions = [rule for rule in rules if rule.companions]
    if not rules_with_companions:
        return all_matches, final_unmatched, companion_candidate_ids

    matched_by_row = {match.transaction.row_number: match for match in all_matches}
    all_txns = [match.transaction for match in all_matches] + final_unmatched
    txns_by_rule: dict[str, list[Transaction]] = {}
    for txn in all_txns:
        category, _ = _classify_transaction(txn, rules)
        txns_by_rule.setdefault(category, []).append(txn)

    already_matched_ids = {
        candidate.candidate_id
        for match in all_matches
        for candidate in match.pdf_candidates
    }

    newly_matched: list[MatchResult] = []
    still_unmatched_rows: set[int] = {txn.row_number for txn in final_unmatched}
    seen_groups: set[frozenset[int]] = set()

    for rule in rules_with_companions:
        companion_names = [rule.name] + rule.companions
        companion_txns: list[Transaction] = []
        for name in companion_names:
            companion_txns.extend(txns_by_rule.get(name, []))
        if len(companion_txns) < 2:
            continue

        by_date: dict[str, list[Transaction]] = {}
        for txn in companion_txns:
            date = txn.date_posting or txn.date_value
            if date:
                by_date.setdefault(date, []).append(txn)

        for date, group_txns in by_date.items():
            group_rules = {(_classify_transaction(txn, rules)[0]) for txn in group_txns}
            if len(group_rules) < 2:
                continue
            group_key = frozenset(txn.row_number for txn in group_txns)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)

            group_sum = sum(abs(txn.amount) for txn in group_txns)
            matched_cand = None
            for cand in all_candidates:
                if cand.candidate_id in already_matched_ids:
                    continue
                if cand.total_amount is None:
                    continue
                if abs(group_sum - cand.total_amount) > _amount_tolerance():
                    continue
                days = _days_between(date, cand.date_issued)
                if days is not None and days <= _date_window_days():
                    matched_cand = cand
                    break

            if matched_cand is None:
                continue

            companion_candidate_ids.add(matched_cand.candidate_id)
            already_matched_ids.add(matched_cand.candidate_id)

            for txn in group_txns:
                if txn.row_number in matched_by_row:
                    match = matched_by_row[txn.row_number]
                    existing_ids = {cand.candidate_id for cand in match.pdf_candidates}
                    if matched_cand.candidate_id not in existing_ids:
                        match.pdf_candidates.append(matched_cand)
                elif txn.row_number in still_unmatched_rows:
                    new_match = MatchResult(
                        transaction=txn,
                        pdf_candidates=[matched_cand],
                        method="companion",
                        confidence=1.0,
                        reasoning=f"Companion sum match: {group_sum:.2f}",
                    )
                    newly_matched.append(new_match)
                    still_unmatched_rows.discard(txn.row_number)
                    matched_by_row[txn.row_number] = new_match

    updated_matches = all_matches + newly_matched
    updated_unmatched = [txn for txn in final_unmatched if txn.row_number in still_unmatched_rows]
    return updated_matches, updated_unmatched, companion_candidate_ids


def _write_reconciliation_file(
    excel_path: Path,
    matches: list[MatchResult],
    unmatched_transactions: list[Transaction],
    total_transactions: int,
    *,
    errors: dict[int, list[str]] | None = None,
    unmatched_files: list[PDFCandidate] | None = None,
    rules: list | None = None,
) -> Path:
    errors = errors or {}
    unmatched_files = unmatched_files or []
    rules = rules or []
    output_path = excel_path.with_suffix(".reconciliation.json")

    total_reconciled = sum(1 for match in matches if match.transaction.row_number not in errors)
    total_incomplete = sum(1 for match in matches if match.transaction.row_number in errors)
    total_unmatched = total_transactions - len(matches)
    reconciliation_rate = (total_reconciled / total_transactions * 100) if total_transactions > 0 else 0

    data = {
        "source": excel_path.name,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total": total_transactions,
            "reconciled": total_reconciled,
            "incomplete": total_incomplete,
            "unmatched": total_unmatched,
            "unmatched_files": len(unmatched_files),
            "reconciliation_rate": round(reconciliation_rate, 1),
        },
        "matches": [_serialize_match(match, errors=errors, rules=rules) for match in matches],
        "unmatched": [_serialize_unmatched_transaction(txn, rules) for txn in unmatched_transactions],
        "unmatched_files": [_serialize_unmatched_candidate(cand) for cand in unmatched_files],
    }
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def reconcile_single(
    runtime: Runtime,
    repository: DocumentRepository,
    export_path: Path,
    excel_path: Path,
    *,
    dry_run: bool,
    quiet: bool = False,
) -> dict:
    console = runtime.console
    empty_stats = {
        "total": 0,
        "reconciled": 0,
        "unmatched": 0,
        "incomplete": 0,
        "unmatched_files": 0,
        "reconciliation_rate": 0.0,
    }

    transactions = _load_transactions(runtime, excel_path)
    if not transactions:
        if not quiet:
            console.warning("No untreated transactions found")
        return empty_stats

    profile = runtime.profile
    policy_token = _ACTIVE_RECONCILIATION_POLICY.set(_policy_from_profile(profile))
    rules = _rules_from_profile(profile)
    exclude_prefixes = profile.reconciliation.exclude_prefixes
    statement_issuing_party = _load_statement_issuing_party(repository, excel_path)
    candidates = _load_reconciliation_candidates(
        repository,
        export_path,
        transactions,
        exclude_prefixes=exclude_prefixes,
        rules=rules,
    )
    matchable = [
        candidate
        for candidate in candidates
        if not candidate.exclude_from_matching
        and not candidate.is_ignored_for_reconciliation
        and _is_candidate_compatible_with_statement_bank(candidate, statement_issuing_party)
    ]

    p1_matches, unmatched_txns, remaining_cands = _phase1_deterministic_match(
        transactions,
        matchable,
        rules,
    )
    p1_matches, unmatched_txns, line_item_candidate_ids = _link_line_item_documents(
        p1_matches,
        unmatched_txns,
        matchable,
        rules,
    )
    p2_matches = (
        _phase2_llm_match(runtime, unmatched_txns, remaining_cands)
        if unmatched_txns and remaining_cands
        else []
    )
    all_matches = p1_matches + p2_matches

    p2_matched_rows = {match.transaction.row_number for match in p2_matches}
    final_unmatched = [txn for txn in unmatched_txns if txn.row_number not in p2_matched_rows]

    all_matches, final_unmatched, companion_candidate_ids = _link_companion_documents(
        all_matches,
        final_unmatched,
        matchable,
        rules,
    )
    all_matches, final_unmatched, shared_candidate_ids = _link_shared_documents(
        all_matches,
        final_unmatched,
        matchable,
        rules,
    )
    all_matches, final_unmatched, via_verde_shared_candidate_ids = _link_via_verde_period_documents(
        all_matches,
        final_unmatched,
        matchable,
        rules,
    )
    shared_candidate_ids.update(via_verde_shared_candidate_ids)
    paired_supporting_candidate_ids = _link_paired_supporting_documents(
        all_matches,
        matchable,
        rules,
    )
    evidence_candidate_ids = _link_evidence_counterparty_documents(all_matches, matchable, rules)
    related_candidate_ids = _link_related_no_amount_documents(
        all_matches,
        matchable,
        rules,
    )
    _prune_unexpected_candidates(all_matches, rules)

    candidate_match_counts: dict[str, int] = {}
    for match in all_matches:
        for cand in match.pdf_candidates:
            candidate_match_counts[cand.candidate_id] = candidate_match_counts.get(cand.candidate_id, 0) + 1
    for candidate_id, count in candidate_match_counts.items():
        if count > 1:
            is_sub_doc = any(
                candidate.candidate_id == candidate_id and candidate.is_sub_document
                for match in all_matches
                for candidate in match.pdf_candidates
            )
            if (
                not is_sub_doc
                and candidate_id not in shared_candidate_ids
                and candidate_id not in companion_candidate_ids
                and candidate_id not in line_item_candidate_ids
                and candidate_id not in paired_supporting_candidate_ids
                and candidate_id not in evidence_candidate_ids
                and candidate_id not in related_candidate_ids
            ):
                logger.warning(f"[REDUNDANT-MATCH] {candidate_id} matched {count} transactions")

    matched_candidate_ids = {
        candidate.candidate_id
        for match in all_matches
        for candidate in match.pdf_candidates
    }
    matched_shared_period_filenames = {
        candidate.pdf_filename
        for match in all_matches
        for candidate in match.pdf_candidates
        if candidate.is_shared_period_document
    }
    unmatched_files = [
        candidate
        for candidate in matchable
        if candidate.candidate_id not in matched_candidate_ids
        and candidate.pdf_filename not in matched_shared_period_filenames
        and _candidate_belongs_to_export_path(candidate, export_path)
        and _candidate_matches_export_month(candidate, export_path)
    ]

    validation_errors = _merge_validation_errors(
        _validate_required_documents(all_matches, rules),
        _validate_supporting_documents_have_bank_pair(
            all_matches,
            statement_issuing_party,
        ),
    )
    for row_num, row_errors in validation_errors.items():
        txn = next(match.transaction for match in all_matches if match.transaction.row_number == row_num)
        category, _ = _classify_transaction(txn, rules)
        logger.debug(f"[INCOMPLETE] Row {row_num} ({category}): {', '.join(row_errors)}")

    if not dry_run:
        _write_reconciliation_file(
            excel_path,
            all_matches,
            final_unmatched,
            len(transactions),
            errors=validation_errors,
            unmatched_files=unmatched_files,
            rules=rules,
        )
    elif not quiet:
        console.detail(f"Dry run: would write {len(all_matches)} matches to .reconciliation file")

    total_txns = len(transactions)
    total_unmatched = len(final_unmatched)
    total_incomplete = len(validation_errors)
    total_reconciled = len(all_matches) - total_incomplete
    total_unmatched_files = len(unmatched_files)
    pct = (total_reconciled / total_txns * 100) if total_txns > 0 else 0

    if not quiet:
        console.info("")
        console.success(f"{total_reconciled}/{total_txns} transactions reconciled ({pct:.1f}%)", indent=False)
        if total_incomplete > 0:
            console.warning(f"{total_incomplete} matched transactions with errors", indent=False)
            for row_num, row_errors in validation_errors.items():
                console.detail(f"Row {row_num}: {', '.join(row_errors)}")
        if total_unmatched > 0:
            console.warning(f"{total_unmatched} transactions unmatched", indent=False)
            for txn in final_unmatched:
                console.detail(f"Row {txn.row_number}: {txn.description[:50]} ({txn.amount:.2f} {txn.currency})")
        if total_unmatched_files > 0:
            console.warning(f"{total_unmatched_files} document files unmatched", indent=False)
            for cand in unmatched_files:
                amount_str = ""
                if cand.total_amount is not None:
                    currency = cand.total_amount_currency or _default_currency()
                    amount_str = f" ({cand.total_amount:.2f} {currency})"
                console.detail(f"{cand.pdf_filename}{amount_str}")

    result = {
        "total": total_txns,
        "reconciled": total_reconciled,
        "unmatched": total_unmatched,
        "incomplete": total_incomplete,
        "unmatched_files": total_unmatched_files,
        "reconciliation_rate": pct,
        "matches": all_matches,
    }
    _ACTIVE_RECONCILIATION_POLICY.reset(policy_token)
    return result
