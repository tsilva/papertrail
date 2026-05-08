"""Typed configuration loading and environment helpers."""

from __future__ import annotations

import shutil
import urllib.error
import urllib.request
from pathlib import Path

import openai
from pydantic import BaseModel, ConfigDict, Field, field_validator

from papertrail.reconciliation_defaults import (
    DEFAULT_AMOUNT_TOLERANCE,
    DEFAULT_BANK_COUNTERPARTIES,
    DEFAULT_BANK_EXPORT_PREFIX,
    DEFAULT_BANK_GENERATED_DOC_TYPES,
    DEFAULT_COUNTERPARTY_ALIASES,
    DEFAULT_DATE_WINDOW_DAYS,
    DEFAULT_EVIDENCE_COUNTERPARTY_CATEGORIES,
    DEFAULT_EVIDENCE_COUNTERPARTY_REQUIRED_PATTERN,
    DEFAULT_LINE_ITEM_CATEGORY_ALIASES,
    DEFAULT_LINE_ITEM_EXTRACTORS,
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


class OpenRouterSettings(SettingsModel):
    model_id: str | None = None
    api_key: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"


class GmailSettings(SettingsModel):
    enabled: bool = False
    attachment_mime_types: list[str] = Field(default_factory=lambda: ["application/pdf"])
    label_filter: str | None = None
    max_results_per_query: int = 500
    skip_already_downloaded: bool = True
    credentials_file: str | None = None
    token_file: str | None = None


class PasswordSettings(SettingsModel):
    passwords: list[str] = Field(default_factory=list)
    passwords_file: str | None = None


class NIFAPISettings(SettingsModel):
    enabled: bool = False
    enabled_locales: list[str] = Field(default_factory=lambda: ["pt-PT"])


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
    exclude_prefixes: list[str] = Field(default_factory=list)
    rules: list[ReconciliationRule] = Field(default_factory=list)
    amount_tolerance: float = DEFAULT_AMOUNT_TOLERANCE
    date_window_days: int = DEFAULT_DATE_WINDOW_DAYS
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
    bank_counterparties: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BANK_COUNTERPARTIES)
    )
    counterparty_aliases: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_COUNTERPARTY_ALIASES)
    )
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
    line_item_category_aliases: dict[str, dict[str, object]] = Field(
        default_factory=lambda: {
            name: dict(settings)
            for name, settings in DEFAULT_LINE_ITEM_CATEGORY_ALIASES.items()
        }
    )
    line_item_extractors: dict[str, dict[str, object]] = Field(
        default_factory=lambda: {
            name: dict(settings)
            for name, settings in DEFAULT_LINE_ITEM_EXTRACTORS.items()
        }
    )


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


class ExportSettings(SettingsModel):
    file_mappings: FileMappingSettings = Field(default_factory=FileMappingSettings)
    merge_rules: list[MergeRule] = Field(default_factory=list)
    max_file_size_mb: float | None = None


class ProfileSettings(SettingsModel):
    profile: ProfileInfo
    paths: PathsSettings = Field(default_factory=PathsSettings)
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    gmail: GmailSettings = Field(default_factory=GmailSettings)
    passwords: PasswordSettings = Field(default_factory=PasswordSettings)
    nif_api: NIFAPISettings = Field(default_factory=NIFAPISettings)
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


def _normalize_profile_data(data: dict[str, object], profile_path: Path | None) -> dict[str, object]:
    normalized = dict(data)

    defaults = {
        "profile": {},
        "paths": {},
        "openrouter": {},
        "gmail": {},
        "passwords": {},
        "nif_api": {},
        "classification": {},
        "reconciliation": {},
        "export": {},
    }
    for key, value in defaults.items():
        normalized.setdefault(key, value)

    gmail = dict(normalized["gmail"])
    settings = gmail.pop("settings", {})
    if isinstance(settings, dict):
        for key, value in settings.items():
            gmail.setdefault(key, value)
    normalized["gmail"] = gmail

    profile = normalized["profile"]
    profile.setdefault("description", "")

    openrouter = normalized["openrouter"]
    openrouter.setdefault("base_url", "https://openrouter.ai/api/v1")

    gmail.setdefault("enabled", False)
    gmail.setdefault("attachment_mime_types", ["application/pdf"])
    gmail.setdefault("max_results_per_query", 500)
    gmail.setdefault("skip_already_downloaded", True)

    normalized["nif_api"].setdefault("enabled", False)
    normalized["nif_api"].setdefault("enabled_locales", ["pt-PT"])
    normalized["classification"].setdefault("document_type_overrides", ClassificationSettings().document_type_overrides)
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

    paths = normalized["paths"]
    raw = paths.get("raw")
    if isinstance(raw, str):
        paths["raw"] = [raw]
    elif raw is None:
        paths["raw"] = []

    reconciliation = normalized["reconciliation"]
    reconciliation.setdefault("exclude_prefixes", [])
    reconciliation.setdefault("rules", [])

    export = normalized["export"]
    export.setdefault("file_mappings", {})
    export["file_mappings"].setdefault("enabled", False)
    export["file_mappings"].setdefault("default_prefix", "")
    export["file_mappings"].setdefault("rules", [])
    export["file_mappings"].setdefault("filename_fields", [])
    export.setdefault("merge_rules", [])
    export.setdefault("max_file_size_mb", None)

    if profile_path:
        if paths.get("raw"):
            paths["raw"] = [_resolve_path(path, profile_path) for path in paths["raw"]]
        paths["processed"] = _resolve_path(paths.get("processed"), profile_path)
        paths["export"] = _resolve_path(paths.get("export"), profile_path)

        passwords = normalized["passwords"]
        passwords["passwords_file"] = _resolve_path(passwords.get("passwords_file"), profile_path)
        if gmail.get("credentials_file"):
            gmail["credentials_file"] = _resolve_path(gmail["credentials_file"], profile_path)
        if gmail.get("token_file"):
            gmail["token_file"] = _resolve_path(gmail["token_file"], profile_path)

    normalized["profile_path"] = profile_path
    normalized["profile_dir"] = profile_path.parent if profile_path else None
    return normalized


def get_profiles_dir() -> Path:
    profiles_dir = Path.home() / ".config" / "papertrail" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    return profiles_dir


def get_cache_dir() -> Path:
    cache_dir = Path.home() / ".config" / "papertrail" / "cache"
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
    credentials_dir = Path.home() / ".config" / "papertrail" / "credentials"
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

    user_profiles = Path.home() / ".config" / "papertrail" / "profiles"
    user_cache = Path.home() / ".config" / "papertrail" / "cache"
    user_credentials = Path.home() / ".config" / "papertrail" / "credentials"

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
