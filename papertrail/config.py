"""Typed configuration loading and environment helpers."""

from __future__ import annotations

import os
import shutil
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

import openai
from pydantic import BaseModel, ConfigDict, Field, field_validator

from papertrail.qr.models import (
    DEFAULT_PORTUGUESE_INVOICE_DOCUMENT_TYPE_CODES,
    DEFAULT_QR_COUNTRY_CODE,
    DEFAULT_QR_CURRENCY,
    DEFAULT_QR_CURRENCY_BY_COUNTRY,
)
from papertrail.reconciliation_defaults import (
    DEFAULT_AMOUNT_TOLERANCE,
    DEFAULT_BANK_COUNTERPARTIES,
    DEFAULT_BANK_EXPORT_PREFIX,
    DEFAULT_BANK_GENERATED_DOC_TYPES,
    DEFAULT_DATE_WINDOW_DAYS,
    DEFAULT_DOCUMENT_FAMILIES,
    DEFAULT_EVIDENCE_COUNTERPARTY_AMOUNT_OPTIONAL_CATEGORIES,
    DEFAULT_EVIDENCE_COUNTERPARTY_CATEGORIES,
    DEFAULT_EVIDENCE_COUNTERPARTY_REQUIRED_PATTERN,
    DEFAULT_EVIDENCE_COUNTERPARTY_SKIP_IF_SUPPLIER_PRESENT_CATEGORIES,
    DEFAULT_LLM_MATCH_CONFIDENCE,
    DEFAULT_RECONCILIATION_CURRENCY,
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

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


class ConfigError(Exception):
    """Base exception for configuration errors."""


class ProfileNotFoundError(ConfigError):
    """Raised when a requested profile cannot be found."""


class ProfileParseError(ConfigError):
    """Raised when a profile file cannot be parsed."""


class SettingsModel(BaseModel):
    """Base model for mutable settings sections."""

    model_config = ConfigDict(
        extra="allow",
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )


class ProfileInfo(SettingsModel):
    name: str
    description: str = ""
    tax_number: str | None = None


class PathsSettings(SettingsModel):
    raw: list[str] = Field(default_factory=list)
    processed: str | None = None
    export: str | None = None

    @field_validator("raw", mode="before")
    @classmethod
    def _normalize_raw(_cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)


RuleCardinality = int | list[int | None]
ExportMatchValue = str | int | float | bool
ExpectedPageCount = int | list[int]


DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
DEFAULT_GMAIL_EXTENSION_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
}
DEFAULT_GMAIL_GENERIC_MIME_TYPES = [
    "application/octet-stream",
    "application/binary",
    "application/force-download",
    "application/x-download",
]


class OpenRouterRequestSettings(SettingsModel):
    classification_max_tokens: int = 4096
    classification_temperature: float = 0.0
    classification_retries: int = 2
    normalization_max_tokens: int = 256
    normalization_temperature: float = 0.0
    reconciliation_max_tokens: int = 4096
    reconciliation_temperature: float = 0.0
    api_probe_timeout_seconds: int = 10


class OpenRouterSettings(SettingsModel):
    model_id: str | None = None
    api_key: str | None = None
    base_url: str = DEFAULT_OPENROUTER_BASE_URL
    requests: OpenRouterRequestSettings = Field(default_factory=OpenRouterRequestSettings)


class GmailSettings(SettingsModel):
    enabled: bool = False
    default_months: int = 2
    output_subdir: str = "gmail"
    output_raw_path: str | None = None
    tracking_dir: str | None = None
    tracking_subdir: str = "gmail_tracking"
    attachment_mime_types: list[str] = Field(default_factory=lambda: ["application/pdf"])
    label_filter: str | None = None
    max_results_per_query: int = 500
    skip_already_downloaded: bool = True
    credentials_file: str | None = None
    token_file: str | None = None
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_GMAIL_SCOPES))
    extension_mime_types: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_GMAIL_EXTENSION_MIME_TYPES)
    )
    generic_mime_types: list[str] = Field(
        default_factory=lambda: list(DEFAULT_GMAIL_GENERIC_MIME_TYPES)
    )
    api_service: str = "gmail"
    api_version: str = "v1"
    api_page_size: int = 100
    subject_slug_max_chars: int = 80


class PasswordSettings(SettingsModel):
    passwords: list[str] = Field(default_factory=list)
    passwords_file: str | None = None


class NIFAPISettings(SettingsModel):
    enabled: bool = False
    enabled_locales: list[str] = Field(default_factory=lambda: ["pt-PT"])
    base_url: str = "https://www.nif.pt/{nif}/"
    timeout_seconds: int = 10
    cache_path: str | None = None
    country_prefixes: list[str] = Field(default_factory=lambda: [DEFAULT_QR_COUNTRY_CODE])


class RenderSettings(SettingsModel):
    max_pages: int = 2
    enhance_contrast: bool = True
    contrast_factor: float = 2.0


class InputSettings(SettingsModel):
    extensions: list[str] = Field(
        default_factory=lambda: [
            ".pdf",
            ".xlsx",
            ".png",
            ".jpg",
            ".jpeg",
            ".tiff",
            ".tif",
            ".bmp",
            ".webp",
        ]
    )
    image_extensions: list[str] = Field(
        default_factory=lambda: [
            ".png",
            ".jpg",
            ".jpeg",
            ".tiff",
            ".tif",
            ".bmp",
            ".webp",
        ]
    )
    skip_dirs: list[str] = Field(default_factory=lambda: ["logs"])
    skip_dir_prefixes: list[str] = Field(default_factory=lambda: ["_dupes"])
    skip_hidden_files: bool = True


class BundleSettings(SettingsModel):
    enabled: bool = True
    pagination_patterns: list[str] = Field(
        default_factory=lambda: [r"P[aá]g\.?\s*(\d+)\s*/\s*(\d+)"]
    )


class QRSettings(SettingsModel):
    enabled: bool = True
    dpi: int = 300
    max_pages: int = 5
    include_last: bool = True
    currency_by_country: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_QR_CURRENCY_BY_COUNTRY)
    )
    default_currency: str = DEFAULT_QR_CURRENCY
    document_type_codes: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_PORTUGUESE_INVOICE_DOCUMENT_TYPE_CODES)
    )


class HashingSettings(SettingsModel):
    fast_chunk_size: int = 8192
    content_dpi: int = 150
    text_min_chars: int = 50


class ProcessingSettings(SettingsModel):
    input: InputSettings = Field(default_factory=InputSettings)
    bundle: BundleSettings = Field(default_factory=BundleSettings)
    render: RenderSettings = Field(default_factory=RenderSettings)
    qr: QRSettings = Field(default_factory=QRSettings)
    hashing: HashingSettings = Field(default_factory=HashingSettings)


class WorkflowSettings(SettingsModel):
    default_months: int = 2
    sync_workers: int = 1
    metadata_load_workers: int = 16


class ToolSettings(SettingsModel):
    default_profile: str = "default"
    preview_dpi: int = 150
    xlsx_preview_max_rows: int = 100
    llm_high_confidence_threshold: float = 0.8


class NamingSettings(SettingsModel):
    component_max_chars: int = 80
    pdf_export_max_chars: int = 60
    filename_warning_max_chars: int = 60


class DependenciesSettings(SettingsModel):
    zbar_library_paths: list[str] = Field(default_factory=list)


class BankStatementsSettings(SettingsModel):
    formats: dict[str, dict[str, object]] = Field(
        default_factory=lambda: {
            "millennium_bcp": {
                "header_row": 8,
                "data_start_row": 9,
                "scan_columns": 7,
                "expected_headers": ["data lancamento", "descricao", "montante"],
                "date_formats": ["%d/%m/%Y", "%d-%m-%Y"],
                "account_cell": [2, 3],
                "period_start_cell": [3, 3],
                "period_end_cell": [4, 3],
                "account_currency_separator": " - ",
                "default_currency": "EUR",
                "issuer_party": "MillenniumBCP",
                "issuer_party_raw": "Millennium BCP",
                "max_columns": 7,
                "description_column": 3,
                "amount_column": 4,
                "currency_column": 5,
                "notes_column": 6,
                "treated_column": 7,
                "untreated_values": ["nao", "não", ""],
            },
            "bpi": {
                "header_row": 18,
                "data_start_row": 19,
                "scan_columns": 7,
                "expected_headers": ["data mov.", "descricao do movimento", "valor em eur"],
                "date_formats": ["%d-%m-%Y", "%d/%m/%Y"],
                "account_cell": [7, 3],
                "account_currency_pattern": r"([\d\-.]+)\s*\((\w+)\)",
                "default_currency": "EUR",
                "issuer_party": "BPI",
                "issuer_party_raw": "BPI",
                "max_columns": 4,
                "description_column": 3,
                "amount_column": 4,
            },
        }
    )


class DocumentTypeOverride(SettingsModel):
    target: str
    raw_types: list[str] = Field(default_factory=list)
    context_all: list[str] = Field(default_factory=list)
    context_any: list[str] = Field(default_factory=list)


class ClassificationSettings(SettingsModel):
    document_type_overrides: list[DocumentTypeOverride] = Field(
        default_factory=lambda: [
            {"target": "bank-note", "raw_types": ["movimento", "notadelancamento"]},
            {
                "target": "investment-acquisition-summary",
                "context_all": ["mapa", "resumo", "datas", "valores", "aquisicao", "mobiliarios"],
            },
            {
                "target": "loan-simulation",
                "raw_types": ["simulacao", "simulation"],
                "context_any": ["credito", "credit", "loan", "emprestimo", "financiamento"],
            },
            {"target": "bank-note", "context_all": ["concess", "cred", "empr"]},
        ]
    )
    prompt_document_type_rules: list[str] = Field(
        default_factory=lambda: [
            "For a credit/loan product simulation (e.g. 'Simulação Crédito Digital Finance Desk'), use loan-simulation",
            "For Millennium/BCP loan disbursement movement details with wording like 'CONCESS CRED EMPR MN', use bank-note, not bank-transfer. Do not treat navigation/counterparty text such as 'TRF P/ ... - BPI' as the issuer or title.",
        ]
    )
    prompt_issuing_party_rules: list[str] = Field(
        default_factory=lambda: [
            "For bank notes, movements, and payment confirmations, issuing_party is the bank or financial institution that generated the document. Do not use the merchant, payee, beneficiary, destination bank, or counterparty as issuing_party; keep those details in document_title or reasoning instead",
            "For Portuguese bank-generated documents, use Banco BPI/BPI -> BPI and Millennium bcp/Banco Comercial Português/BCP -> MillenniumBCP when visible",
        ]
    )
    issuer_tax_number_prefix_rule: str = (
        "Include country prefix when visible (e.g., DETESTOWNER). "
        "Omit prefix only for Portuguese documents where no prefix is shown. Null if the issuer tax number is not visible."
    )
    document_title_max_chars: int = 60
    bank_statement_locale: str = "pt-PT"
    legal_suffixes: list[str] = Field(
        default_factory=lambda: [
            "inc",
            "incorporated",
            "ltd",
            "limited",
            "llc",
            "llp",
            "plc",
            "corp",
            "corporation",
            "company",
            "co",
            "sa",
            "lda",
            "pbc",
        ]
    )


class ReconciliationRule(SettingsModel):
    name: str
    match_description: list[str] = Field(default_factory=list)
    direction: str | None = None
    required_types: dict[str, RuleCardinality] = Field(default_factory=dict)
    shared_types: dict[str, str | None] = Field(default_factory=dict)
    shared_filters: dict[str, dict[str, ExportMatchValue]] = Field(default_factory=dict)
    companions: list[str] = Field(default_factory=list)
    expected_page_count: dict[str, ExpectedPageCount] = Field(default_factory=dict)


class ReconciliationSettings(SettingsModel):
    policy_files: list[str] = Field(default_factory=list)
    exclude_prefixes: list[str] = Field(default_factory=list)
    rules: list[ReconciliationRule] = Field(default_factory=list)
    include_builtin_rules: bool = True
    amount_tolerance: float = DEFAULT_AMOUNT_TOLERANCE
    date_window_days: int = DEFAULT_DATE_WINDOW_DAYS
    tax_number_default_country_prefix: str = DEFAULT_TAX_NUMBER_DEFAULT_COUNTRY_PREFIX
    bank_generated_doc_types: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BANK_GENERATED_DOC_TYPES)
    )
    statement_bank_scoped_doc_types: list[str] = Field(
        default_factory=lambda: list(DEFAULT_STATEMENT_BANK_SCOPED_DOC_TYPES)
    )
    statement_bank_issuer_aliases: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_STATEMENT_BANK_ISSUER_ALIASES)
    )
    bank_export_prefix: str = DEFAULT_BANK_EXPORT_PREFIX
    supporting_export_prefixes: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SUPPORTING_EXPORT_PREFIXES)
    )
    supporting_doc_type_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SUPPORTING_DOC_TYPE_PATTERNS)
    )
    document_families: dict[str, dict[str, object]] = Field(
        default_factory=lambda: {
            family: dict(settings)
            for family, settings in DEFAULT_DOCUMENT_FAMILIES.items()
            if isinstance(settings, dict)
        }
    )
    bank_counterparties: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BANK_COUNTERPARTIES)
    )
    include_builtin_counterparty_aliases: bool = True
    counterparty_aliases: dict[str, str] = Field(default_factory=dict)
    shared_period_transaction_keywords: dict[str, list[str]] = Field(
        default_factory=lambda: {
            party: list(keywords)
            for party, keywords in DEFAULT_SHARED_PERIOD_TRANSACTION_KEYWORDS.items()
        }
    )
    shared_period_title_terms: dict[str, list[str]] = Field(
        default_factory=lambda: {
            party: list(terms)
            for party, terms in DEFAULT_SHARED_PERIOD_TITLE_TERMS.items()
        }
    )
    same_month_shared_rule_names: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SAME_MONTH_SHARED_RULE_NAMES)
    )
    strict_statement_banks: list[str] = Field(
        default_factory=lambda: list(DEFAULT_STRICT_STATEMENT_BANKS)
    )
    supporting_pair_exempt_statement_banks: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SUPPORTING_PAIR_EXEMPT_STATEMENT_BANKS)
    )
    shared_period_link_categories: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SHARED_PERIOD_LINK_CATEGORIES)
    )
    shared_period_supplier_evidence_error_exempt_rule_names: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SHARED_PERIOD_SUPPLIER_EVIDENCE_ERROR_EXEMPT_RULE_NAMES)
    )
    shared_period_bank_anchor_error_exempt_rule_names: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SHARED_PERIOD_BANK_ANCHOR_ERROR_EXEMPT_RULE_NAMES)
    )
    evidence_counterparty_categories: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EVIDENCE_COUNTERPARTY_CATEGORIES)
    )
    evidence_counterparty_required_pattern: str = DEFAULT_EVIDENCE_COUNTERPARTY_REQUIRED_PATTERN
    evidence_counterparty_skip_if_supplier_present_categories: list[str] = Field(
        default_factory=lambda: list(
            DEFAULT_EVIDENCE_COUNTERPARTY_SKIP_IF_SUPPLIER_PRESENT_CATEGORIES
        )
    )
    evidence_counterparty_amount_optional_categories: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EVIDENCE_COUNTERPARTY_AMOUNT_OPTIONAL_CATEGORIES)
    )
    default_currency: str = DEFAULT_RECONCILIATION_CURRENCY
    llm_default_confidence: float = DEFAULT_LLM_MATCH_CONFIDENCE
    line_item_category_aliases: dict[str, dict[str, object]] = Field(default_factory=dict)
    include_builtin_line_item_extractors: bool = True
    line_item_extractors: dict[str, dict[str, object]] = Field(default_factory=dict)


class ExportRule(SettingsModel):
    match: dict[str, ExportMatchValue] = Field(default_factory=dict)
    prefix: str = ""


class FileMappingSettings(SettingsModel):
    enabled: bool = False
    default_prefix: str = ""
    rules: list[ExportRule] = Field(default_factory=list)
    filename_fields: list[str] = Field(default_factory=list)


class MergeRule(SettingsModel):
    target_type: str
    attach_type: str


class CompressionSettings(SettingsModel):
    enabled: bool = True
    quality: str = "ebook"
    min_size_mb: float | None = None


class ExportSettings(SettingsModel):
    file_mappings: FileMappingSettings = Field(default_factory=FileMappingSettings)
    merge_rules: list[MergeRule] = Field(default_factory=list)
    max_file_size_mb: float | None = None
    compression: CompressionSettings = Field(default_factory=CompressionSettings)


class ProfileSettings(SettingsModel):
    profile: ProfileInfo
    paths: PathsSettings = Field(default_factory=PathsSettings)
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    gmail: GmailSettings = Field(default_factory=GmailSettings)
    passwords: PasswordSettings = Field(default_factory=PasswordSettings)
    nif_api: NIFAPISettings = Field(default_factory=NIFAPISettings)
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    naming: NamingSettings = Field(default_factory=NamingSettings)
    dependencies: DependenciesSettings = Field(default_factory=DependenciesSettings)
    bank_statements: BankStatementsSettings = Field(default_factory=BankStatementsSettings)
    classification: ClassificationSettings = Field(default_factory=ClassificationSettings)
    reconciliation: ReconciliationSettings = Field(default_factory=ReconciliationSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    profile_path: Path | None = Field(default=None, exclude=True)
    profile_dir: Path | None = Field(default=None, exclude=True)


Profile = ProfileSettings


def _resolve_path(path_str: str | None, profile_path: Path | None) -> str | None:
    if not path_str:
        return None
    path = Path(path_str)
    if path.is_absolute() or profile_path is None:
        return str(path)
    return str((profile_path.parent / path).resolve())


def _deep_merge(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _load_yaml_mapping(path: Path, *, description: str) -> dict[str, object]:
    if yaml is None:
        raise ConfigError("PyYAML is not installed.")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise ConfigError(f"Could not read {description} at {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ProfileParseError(f"Failed to parse {description} at {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ProfileParseError(f"{description} at {path} must be a YAML mapping")
    return data


def _merge_reconciliation_policy_files(
    inline_reconciliation: dict[str, object],
    profile_path: Path | None,
) -> dict[str, object]:
    policy_files = inline_reconciliation.get("policy_files") or []
    if isinstance(policy_files, str):
        policy_files = [policy_files]
    if not policy_files:
        return inline_reconciliation

    merged: dict[str, object] = {}
    for policy_file in policy_files:
        policy_path = Path(str(policy_file)).expanduser()
        if not policy_path.is_absolute() and profile_path is not None:
            policy_path = profile_path.parent / policy_path
        policy_data = _load_yaml_mapping(policy_path, description="reconciliation policy file")
        merged = _deep_merge(merged, policy_data)

    merged = _deep_merge(merged, inline_reconciliation)
    merged["policy_files"] = [str(item) for item in policy_files]
    return merged


def _normalize_profile_data(data: dict[str, object], profile_path: Path | None) -> dict[str, object]:
    normalized = dict(data)

    defaults = {
        "profile": {},
        "paths": {},
        "openrouter": {},
        "gmail": {},
        "passwords": {},
        "nif_api": {},
        "processing": {},
        "workflow": {},
        "tools": {},
        "naming": {},
        "dependencies": {},
        "bank_statements": {},
        "classification": {},
        "reconciliation": {},
        "export": {},
    }
    for key, value in defaults.items():
        normalized.setdefault(key, value)

    if isinstance(normalized.get("reconciliation"), dict):
        normalized["reconciliation"] = _merge_reconciliation_policy_files(
            dict(normalized["reconciliation"]),
            profile_path,
        )

    gmail = dict(normalized["gmail"])
    settings = gmail.pop("settings", {})
    if isinstance(settings, dict):
        for key, value in settings.items():
            gmail.setdefault(key, value)
    normalized["gmail"] = gmail

    profile = normalized["profile"]
    profile.setdefault("description", "")

    openrouter = normalized["openrouter"]
    openrouter.setdefault("base_url", DEFAULT_OPENROUTER_BASE_URL)
    openrouter.setdefault("requests", {})
    openrouter["requests"].setdefault("classification_retries", 2)

    gmail.setdefault("enabled", False)
    gmail.setdefault("default_months", 2)
    gmail.setdefault("output_subdir", "gmail")
    gmail.setdefault("output_raw_path", None)
    gmail.setdefault("tracking_dir", None)
    gmail.setdefault("tracking_subdir", "gmail_tracking")
    gmail.setdefault("attachment_mime_types", ["application/pdf"])
    gmail.setdefault("max_results_per_query", 500)
    gmail.setdefault("skip_already_downloaded", True)
    gmail.setdefault("scopes", list(DEFAULT_GMAIL_SCOPES))
    gmail.setdefault("extension_mime_types", dict(DEFAULT_GMAIL_EXTENSION_MIME_TYPES))
    gmail.setdefault("generic_mime_types", list(DEFAULT_GMAIL_GENERIC_MIME_TYPES))
    gmail.setdefault("api_service", "gmail")
    gmail.setdefault("api_version", "v1")
    gmail.setdefault("api_page_size", 100)
    gmail.setdefault("subject_slug_max_chars", 80)

    normalized["nif_api"].setdefault("enabled", False)
    normalized["nif_api"].setdefault("enabled_locales", ["pt-PT"])
    normalized["nif_api"].setdefault("base_url", "https://www.nif.pt/{nif}/")
    normalized["nif_api"].setdefault("timeout_seconds", 10)
    normalized["nif_api"].setdefault("cache_path", None)
    normalized["nif_api"].setdefault("country_prefixes", [DEFAULT_QR_COUNTRY_CODE])
    normalized["processing"].setdefault("input", {})
    normalized["processing"]["input"].setdefault("extensions", InputSettings().extensions)
    normalized["processing"]["input"].setdefault(
        "image_extensions",
        InputSettings().image_extensions,
    )
    normalized["processing"]["input"].setdefault("skip_dirs", ["logs"])
    normalized["processing"]["input"].setdefault("skip_dir_prefixes", ["_dupes"])
    normalized["processing"]["input"].setdefault("skip_hidden_files", True)
    normalized["processing"].setdefault("bundle", {})
    normalized["processing"]["bundle"].setdefault("enabled", True)
    normalized["processing"]["bundle"].setdefault(
        "pagination_patterns",
        BundleSettings().pagination_patterns,
    )
    normalized["processing"].setdefault("render", {})
    normalized["processing"]["render"].setdefault("max_pages", 2)
    normalized["processing"]["render"].setdefault("enhance_contrast", True)
    normalized["processing"]["render"].setdefault("contrast_factor", 2.0)
    normalized["processing"].setdefault("qr", {})
    normalized["processing"]["qr"].setdefault("enabled", True)
    normalized["processing"]["qr"].setdefault("dpi", 300)
    normalized["processing"]["qr"].setdefault("max_pages", 5)
    normalized["processing"]["qr"].setdefault("include_last", True)
    normalized["processing"]["qr"].setdefault(
        "currency_by_country",
        dict(DEFAULT_QR_CURRENCY_BY_COUNTRY),
    )
    normalized["processing"]["qr"].setdefault("default_currency", DEFAULT_QR_CURRENCY)
    normalized["processing"]["qr"].setdefault(
        "document_type_codes",
        QRSettings().document_type_codes,
    )
    normalized["processing"].setdefault("hashing", {})
    normalized["processing"]["hashing"].setdefault("fast_chunk_size", 8192)
    normalized["processing"]["hashing"].setdefault("content_dpi", 150)
    normalized["processing"]["hashing"].setdefault("text_min_chars", 50)

    normalized["workflow"].setdefault("default_months", 2)
    normalized["workflow"].setdefault("sync_workers", 1)
    normalized["workflow"].setdefault("metadata_load_workers", 16)
    normalized["tools"].setdefault("default_profile", "default")
    normalized["tools"].setdefault("preview_dpi", 150)
    normalized["tools"].setdefault("xlsx_preview_max_rows", 100)
    normalized["tools"].setdefault("llm_high_confidence_threshold", 0.8)
    normalized["naming"].setdefault("component_max_chars", 80)
    normalized["naming"].setdefault("pdf_export_max_chars", 60)
    normalized["naming"].setdefault("filename_warning_max_chars", 60)
    normalized["dependencies"].setdefault("zbar_library_paths", [])
    normalized["bank_statements"].setdefault("formats", BankStatementsSettings().formats)
    normalized["classification"].setdefault(
        "document_type_overrides",
        ClassificationSettings().document_type_overrides,
    )
    normalized["classification"].setdefault(
        "prompt_document_type_rules",
        ClassificationSettings().prompt_document_type_rules,
    )
    normalized["classification"].setdefault(
        "prompt_issuing_party_rules",
        ClassificationSettings().prompt_issuing_party_rules,
    )
    normalized["classification"].setdefault(
        "issuer_tax_number_prefix_rule",
        ClassificationSettings().issuer_tax_number_prefix_rule,
    )
    normalized["classification"].setdefault("document_title_max_chars", 60)
    normalized["classification"].setdefault("bank_statement_locale", "pt-PT")
    normalized["classification"].setdefault(
        "legal_suffixes",
        ClassificationSettings().legal_suffixes,
    )

    paths = normalized["paths"]
    raw = paths.get("raw")
    if isinstance(raw, str):
        paths["raw"] = [raw]
    elif raw is None:
        paths["raw"] = []

    reconciliation = normalized["reconciliation"]
    reconciliation.setdefault("policy_files", [])
    reconciliation.setdefault("exclude_prefixes", [])
    reconciliation.setdefault("rules", [])
    reconciliation.setdefault("include_builtin_rules", True)
    reconciliation.setdefault("include_builtin_counterparty_aliases", True)
    reconciliation.setdefault("counterparty_aliases", {})
    reconciliation.setdefault("include_builtin_line_item_extractors", True)
    reconciliation.setdefault(
        "evidence_counterparty_skip_if_supplier_present_categories",
        list(DEFAULT_EVIDENCE_COUNTERPARTY_SKIP_IF_SUPPLIER_PRESENT_CATEGORIES),
    )
    reconciliation.setdefault(
        "evidence_counterparty_amount_optional_categories",
        list(DEFAULT_EVIDENCE_COUNTERPARTY_AMOUNT_OPTIONAL_CATEGORIES),
    )
    reconciliation.setdefault("default_currency", DEFAULT_RECONCILIATION_CURRENCY)
    reconciliation.setdefault("llm_default_confidence", DEFAULT_LLM_MATCH_CONFIDENCE)

    export = normalized["export"]
    export.setdefault("file_mappings", {})
    export["file_mappings"].setdefault("enabled", False)
    export["file_mappings"].setdefault("default_prefix", "")
    export["file_mappings"].setdefault("rules", [])
    export["file_mappings"].setdefault("filename_fields", [])
    export.setdefault("merge_rules", [])
    export.setdefault("max_file_size_mb", None)
    export.setdefault("compression", {})
    export["compression"].setdefault("enabled", True)
    export["compression"].setdefault("quality", "ebook")
    export["compression"].setdefault("min_size_mb", None)

    if profile_path:
        if paths.get("raw"):
            paths["raw"] = [_resolve_path(path, profile_path) for path in paths["raw"]]
        paths["processed"] = _resolve_path(paths.get("processed"), profile_path)
        paths["export"] = _resolve_path(paths.get("export"), profile_path)

        passwords = normalized["passwords"]
        passwords["passwords_file"] = _resolve_path(passwords.get("passwords_file"), profile_path)
        normalized["nif_api"]["cache_path"] = _resolve_path(
            normalized["nif_api"].get("cache_path"),
            profile_path,
        )
        if gmail.get("credentials_file"):
            gmail["credentials_file"] = _resolve_path(gmail["credentials_file"], profile_path)
        if gmail.get("token_file"):
            gmail["token_file"] = _resolve_path(gmail["token_file"], profile_path)
        if gmail.get("output_raw_path"):
            gmail["output_raw_path"] = _resolve_path(gmail["output_raw_path"], profile_path)
        if gmail.get("tracking_dir"):
            gmail["tracking_dir"] = _resolve_path(gmail["tracking_dir"], profile_path)

    normalized["profile_path"] = profile_path
    normalized["profile_dir"] = profile_path.parent if profile_path else None
    return normalized


def get_config_root() -> Path:
    override = os.environ.get("PAPERTRAIL_HOME")
    if override:
        return Path(override).expanduser()
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / "papertrail"
    return Path.home() / ".config" / "papertrail"


def get_profiles_dir() -> Path:
    profiles_dir = get_config_root() / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    return profiles_dir


def get_cache_dir() -> Path:
    cache_dir = get_config_root() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


class ProfileLoader:
    """Profile discovery and loading."""

    @property
    def profiles_dir(self) -> Path:
        return get_profiles_dir()

    def list_available_profiles(self) -> list[str]:
        profiles_dir = self.profiles_dir
        if not list(profiles_dir.iterdir()):
            _migrate_from_repo()
        if not profiles_dir.exists():
            return []
        return sorted(
            directory.name
            for directory in profiles_dir.iterdir()
            if directory.is_dir() and (directory / "profile.yaml").exists()
        )

    def load_profile(self, name: str) -> ProfileSettings:
        if yaml is None:
            raise ConfigError("PyYAML is not installed.")

        profile_path = self.profiles_dir / name / "profile.yaml"
        if not profile_path.exists():
            available = self.list_available_profiles()
            raise ProfileNotFoundError(
                f"Profile '{name}' not found at {profile_path}. "
                f"Available: {', '.join(available) or 'none'}"
            )

        try:
            with open(profile_path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise ProfileParseError(f"Failed to parse profile '{name}': {exc}") from exc

        if not isinstance(data, dict):
            raise ProfileParseError(f"Profile '{name}' must be a YAML mapping")
        if "profile" not in data or not isinstance(data.get("profile"), dict):
            raise ConfigError("Missing required field: profile")
        if "name" not in data["profile"]:
            raise ConfigError("Missing required field: profile.name")

        normalized = _normalize_profile_data(data, profile_path)
        return ProfileSettings.model_validate(normalized)


def list_available_profiles() -> list[str]:
    return ProfileLoader().list_available_profiles()


def load_profile(name: str) -> ProfileSettings:
    return ProfileLoader().load_profile(name)


def get_passwords_from_profile(profile: ProfileSettings) -> tuple[list[str], str | None]:
    if profile.passwords.passwords:
        return profile.passwords.passwords, None
    if profile.passwords.passwords_file:
        passwords_file = Path(profile.passwords.passwords_file)
        if passwords_file.exists():
            with open(passwords_file, "r", encoding="utf-8") as handle:
                passwords = [line.strip() for line in handle if line.strip()]
            return passwords, str(passwords_file)
    return [], None


def get_gmail_config_paths(profile: ProfileSettings | None = None) -> dict[str, Path]:
    credentials_dir = get_config_root() / "credentials"
    credentials_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "credentials": credentials_dir / "gmail_credentials.json",
        "token": credentials_dir / "gmail_token.json",
        "settings": credentials_dir / "gmail_settings.json",
    }
    if profile and profile.gmail.enabled:
        if profile.gmail.credentials_file:
            paths["credentials"] = Path(profile.gmail.credentials_file)
        if profile.gmail.token_file:
            paths["token"] = Path(profile.gmail.token_file)
    return paths


def build_openai_client(profile: ProfileSettings) -> openai.OpenAI:
    api_key = profile.openrouter.api_key
    if not api_key:
        raise RuntimeError(
            f"No OpenRouter API key configured in profile '{profile.profile.name}'. "
            "Set openrouter.api_key in your profile YAML."
        )
    return openai.OpenAI(api_key=api_key, base_url=profile.openrouter.base_url)


def check_api_accessibility(base_url: str, timeout: int = 10) -> bool:
    try:
        request = urllib.request.Request(base_url, method="HEAD")
        urllib.request.urlopen(request, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError):
        return False


def _migrate_from_repo() -> None:
    import logging

    logger = logging.getLogger("papertrail.migration")
    repo_root = Path(__file__).parent.parent
    repo_profiles = repo_root / "profiles"
    repo_cache = repo_root / ".cache"
    repo_credentials = repo_root / ".credentials"

    config_root = get_config_root()
    user_profiles = config_root / "profiles"
    user_cache = config_root / "cache"
    user_credentials = config_root / "credentials"

    migrated: list[str] = []

    if repo_profiles.exists() and user_profiles.exists():
        profile_dirs = [
            directory
            for directory in repo_profiles.iterdir()
            if directory.is_dir() and (directory / "profile.yaml").exists()
        ]
        if profile_dirs and not list(user_profiles.iterdir()):
            logger.info(
                "[MIGRATION] Migrating profiles from repo to ~/.config/papertrail/profiles/"
            )
            for profile_dir in profile_dirs:
                destination = user_profiles / profile_dir.name
                shutil.copytree(profile_dir, destination)
                shutil.rmtree(profile_dir)
                migrated.append(f"profile: {profile_dir.name}")

            template = repo_profiles / "profile.yaml.example"
            if template.exists():
                shutil.copy2(template, user_profiles / "profile.yaml.example")
                template.unlink()
                migrated.append("profile.yaml.example")

            try:
                repo_profiles.rmdir()
            except OSError:
                pass

    if repo_cache.exists():
        user_cache.mkdir(parents=True, exist_ok=True)
        for cache_file in ["hash_cache.yaml", "nif_cache.yaml", ".extract.lock"]:
            src = repo_cache / cache_file
            if src.exists():
                shutil.copy2(src, user_cache / cache_file)
                src.unlink()
                migrated.append(f"cache: {cache_file}")

        try:
            remaining = [path for path in repo_cache.iterdir() if not path.name.endswith(".example")]
            if not remaining:
                for path in repo_cache.iterdir():
                    path.unlink()
                repo_cache.rmdir()
        except OSError:
            pass

    if repo_credentials.exists():
        user_credentials.mkdir(parents=True, exist_ok=True)
        for credential in ["gmail_credentials.json", "gmail_token.json", "gmail_settings.json"]:
            src = repo_credentials / credential
            if src.exists():
                shutil.copy2(src, user_credentials / credential)
                src.unlink()
                migrated.append(f"credentials: {credential}")

        try:
            if not list(repo_credentials.iterdir()):
                repo_credentials.rmdir()
        except OSError:
            pass

    if migrated:
        logger.info(f"[MIGRATION] Complete. Migrated: {', '.join(migrated)}")
