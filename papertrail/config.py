"""Typed configuration loading and environment helpers."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

import openai
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from papertrail.document_types import DEFAULT_DOCUMENT_TYPE_OVERRIDES
from papertrail.qr.models import (
    DEFAULT_PORTUGUESE_INVOICE_DOCUMENT_TYPE_CODES,
    DEFAULT_QR_COUNTRY_CODE,
    DEFAULT_QR_CURRENCY,
    DEFAULT_QR_CURRENCY_BY_COUNTRY,
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


def _load_bundled_reconciliation_policy() -> dict[str, object]:
    policy_path = Path(__file__).with_name("reconciliation_policy.yaml")
    if yaml is None:
        raise ConfigError("PyYAML is not installed.")
    try:
        with open(policy_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(
            f"Could not load bundled reconciliation policy at {policy_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Bundled reconciliation policy at {policy_path} must be a YAML mapping")
    return data


_BUNDLED_RECONCILIATION_POLICY = _load_bundled_reconciliation_policy()


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
        default_factory=lambda: [dict(override) for override in DEFAULT_DOCUMENT_TYPE_OVERRIDES]
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
    @model_validator(mode="before")
    @classmethod
    def _merge_bundled_policy(cls, value: object) -> dict[str, object]:
        overlay = dict(value) if isinstance(value, dict) else {}
        base = _BUNDLED_RECONCILIATION_POLICY
        if overlay.get("include_builtin_line_item_extractors") is False:
            base = {**base, "line_item_extractors": {}}
        return _merge_reconciliation_policy_data(base, overlay)

    policy_files: list[str] = Field(default_factory=list)
    exclude_prefixes: list[str] = Field(default_factory=list)
    rules: list[ReconciliationRule] = Field(default_factory=list)
    include_builtin_rules: bool = True
    amount_tolerance: float = 0.01
    date_window_days: int = 30
    tax_number_default_country_prefix: str = "PT"
    bank_generated_doc_types: list[str] = Field(default_factory=list)
    statement_bank_scoped_doc_types: list[str] = Field(default_factory=list)
    statement_bank_issuer_aliases: dict[str, str] = Field(default_factory=dict)
    bank_export_prefix: str = "BNC_"
    supporting_export_prefixes: list[str] = Field(default_factory=list)
    supporting_doc_type_patterns: list[str] = Field(default_factory=list)
    document_families: dict[str, dict[str, object]] = Field(default_factory=dict)
    bank_counterparties: list[str] = Field(default_factory=list)
    counterparty_aliases: dict[str, str] = Field(default_factory=dict)
    shared_period_transaction_keywords: dict[str, list[str]] = Field(default_factory=dict)
    shared_period_title_terms: dict[str, list[str]] = Field(default_factory=dict)
    same_month_shared_rule_names: list[str] = Field(default_factory=list)
    strict_statement_banks: list[str] = Field(default_factory=list)
    supporting_pair_exempt_statement_banks: list[str] = Field(default_factory=list)
    shared_period_link_categories: list[str] = Field(default_factory=list)
    shared_period_supplier_evidence_error_exempt_rule_names: list[str] = Field(
        default_factory=list
    )
    shared_period_bank_anchor_error_exempt_rule_names: list[str] = Field(default_factory=list)
    evidence_counterparty_categories: list[str] = Field(default_factory=list)
    evidence_counterparty_required_pattern: str = "invoice"
    evidence_counterparty_skip_if_supplier_present_categories: list[str] = Field(
        default_factory=list
    )
    evidence_counterparty_amount_optional_categories: list[str] = Field(default_factory=list)
    default_currency: str = "EUR"
    llm_default_confidence: float = 0.5
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


_ADDITIVE_RECONCILIATION_MAPS = {
    "document_families",
    "line_item_category_aliases",
    "line_item_extractors",
}


def _merge_reconciliation_policy_data(
    base: dict[str, object],
    overlay: dict[str, object],
) -> dict[str, object]:
    """Merge a policy overlay while extending list values in structured maps."""
    merged = _deep_merge(base, overlay)
    for map_name in _ADDITIVE_RECONCILIATION_MAPS:
        base_map = base.get(map_name)
        overlay_map = overlay.get(map_name)
        merged_map = merged.get(map_name)
        if not all(isinstance(value, dict) for value in (base_map, overlay_map, merged_map)):
            continue
        for item_name, overlay_settings in overlay_map.items():
            base_settings = base_map.get(item_name)
            merged_settings = merged_map.get(item_name)
            if not all(
                isinstance(value, dict)
                for value in (base_settings, overlay_settings, merged_settings)
            ):
                continue
            for key, overlay_value in overlay_settings.items():
                base_value = base_settings.get(key)
                if isinstance(base_value, list) and isinstance(overlay_value, list):
                    merged_settings[key] = list(dict.fromkeys([*base_value, *overlay_value]))
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
    normalized = deepcopy(data)

    if isinstance(normalized.get("reconciliation"), dict):
        normalized["reconciliation"] = _merge_reconciliation_policy_files(
            dict(normalized["reconciliation"]),
            profile_path,
        )

    gmail = dict(normalized.get("gmail") or {})
    settings = gmail.pop("settings", {})
    if isinstance(settings, dict):
        for key, value in settings.items():
            gmail.setdefault(key, value)
    if gmail or "gmail" in normalized:
        normalized["gmail"] = gmail

    paths = dict(normalized.get("paths") or {})
    raw = paths.get("raw")
    if isinstance(raw, str):
        paths["raw"] = [raw]
    if paths or "paths" in normalized:
        normalized["paths"] = paths

    if profile_path:
        raw_paths = paths.get("raw") or []
        if raw_paths:
            paths["raw"] = [_resolve_path(path, profile_path) for path in raw_paths]
        paths["processed"] = _resolve_path(paths.get("processed"), profile_path)
        paths["export"] = _resolve_path(paths.get("export"), profile_path)
        normalized["paths"] = paths

        passwords = dict(normalized.get("passwords") or {})
        passwords["passwords_file"] = _resolve_path(passwords.get("passwords_file"), profile_path)
        if passwords or "passwords" in normalized:
            normalized["passwords"] = passwords

        nif_api = dict(normalized.get("nif_api") or {})
        nif_api["cache_path"] = _resolve_path(
            nif_api.get("cache_path"),
            profile_path,
        )
        if nif_api or "nif_api" in normalized:
            normalized["nif_api"] = nif_api

        if gmail.get("credentials_file"):
            gmail["credentials_file"] = _resolve_path(gmail["credentials_file"], profile_path)
        if gmail.get("token_file"):
            gmail["token_file"] = _resolve_path(gmail["token_file"], profile_path)
        if gmail.get("output_raw_path"):
            gmail["output_raw_path"] = _resolve_path(gmail["output_raw_path"], profile_path)
        if gmail.get("tracking_dir"):
            gmail["tracking_dir"] = _resolve_path(gmail["tracking_dir"], profile_path)
        if gmail or "gmail" in normalized:
            normalized["gmail"] = gmail

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
