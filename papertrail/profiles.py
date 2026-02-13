"""Profile-based configuration system for papertrail."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore



class ProfileError(Exception):
    """Base exception for profile-related errors."""
    pass


class ProfileNotFoundError(ProfileError):
    """Raised when a requested profile cannot be found."""
    pass


class ProfileParseError(ProfileError):
    """Raised when a profile file cannot be parsed."""
    pass


class ProfileValidationError(ProfileError):
    """Raised when a profile is missing required fields."""
    pass



@dataclass
class ProfileMetadata:
    """Profile metadata (name, description)."""
    name: str
    description: str = ""
    tax_number: Optional[str] = None


@dataclass
class PathsConfig:
    """Path configuration for raw, processed, and export directories."""
    raw: List[str] = field(default_factory=list)
    processed: Optional[str] = None
    export: Optional[str] = None


@dataclass
class OpenRouterConfig:
    """OpenRouter API configuration."""
    model_id: Optional[str] = None
    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"


@dataclass
class DocumentTypesConfig:
    """Document types configuration."""
    predefined: Optional[List[str]] = None


@dataclass
class GmailConfig:
    """Gmail integration configuration."""
    enabled: bool = False
    credentials_file: Optional[str] = None
    token_file: Optional[str] = None
    # Settings (flattened from GmailSettingsConfig)
    attachment_mime_types: List[str] = field(default_factory=lambda: ["application/pdf"])
    label_filter: Optional[str] = None
    max_results_per_query: int = 500
    skip_already_downloaded: bool = True


@dataclass
class PasswordsConfig:
    """Password configuration for ZIP extraction."""
    passwords: Optional[List[str]] = None
    passwords_file: Optional[str] = None


@dataclass
class PipelineConfig:
    """Pipeline task configuration."""
    tools_required: List[str] = field(default_factory=list)
    default_export_date: str = "last_month"


@dataclass
class NifApiConfig:
    """NIF lookup configuration for Portuguese tax number lookups."""
    enabled: bool = False


@dataclass
class ReconciliationRule:
    """A single reconciliation validation rule."""
    name: str
    match_description: List[str] = field(default_factory=list)
    direction: Optional[str] = None  # "credit" or "debit"
    required_types: Dict[str, Any] = field(default_factory=dict)  # pattern → cardinality
    shared_types: Dict[str, Optional[str]] = field(default_factory=dict)  # type pattern → issuing_party filter


@dataclass
class ReconciliationConfig:
    """Reconciliation validation configuration."""
    rules: List[ReconciliationRule] = field(default_factory=list)


@dataclass
class ExportMappingRule:
    """A single prefix mapping rule for export files."""
    match: Dict[str, str]
    prefix: str


@dataclass
class ExportFileMappingsConfig:
    """Configuration for applying prefixes to exported files."""
    enabled: bool = False
    default_prefix: str = ""
    filename_fields: Optional[List[str]] = None
    rules: List[ExportMappingRule] = field(default_factory=list)


@dataclass
class ExportConfig:
    """Export configuration."""
    max_file_size_mb: Optional[float] = None
    file_mappings: ExportFileMappingsConfig = field(default_factory=ExportFileMappingsConfig)


@dataclass
class Profile:
    """Complete profile configuration."""
    profile: ProfileMetadata
    paths: PathsConfig = field(default_factory=PathsConfig)
    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    document_types: DocumentTypesConfig = field(default_factory=DocumentTypesConfig)
    gmail: GmailConfig = field(default_factory=GmailConfig)
    passwords: PasswordsConfig = field(default_factory=PasswordsConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    nif_api: NifApiConfig = field(default_factory=NifApiConfig)
    reconciliation: ReconciliationConfig = field(default_factory=ReconciliationConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    task_defaults: Dict[str, Any] = field(default_factory=dict)
    _profile_path: Optional[Path] = field(default=None, repr=False)

    @property
    def profile_dir(self) -> Optional[Path]:
        """Get the directory containing this profile's config and data files."""
        if self._profile_path:
            return self._profile_path.parent
        return None



def resolve_path(path_str: Optional[str], profile_path: Path) -> Optional[str]:
    """Resolve a path string relative to the profile file location."""
    if not path_str:
        return None
    path = Path(path_str)
    if path.is_absolute():
        return str(path)
    return str((profile_path.parent / path).resolve())


def resolve_paths_in_profile(profile: Profile) -> None:
    """Resolve all relative paths in a profile to absolute paths."""
    if profile._profile_path is None:
        return

    pp = profile._profile_path

    # Resolve path lists
    if profile.paths.raw:
        profile.paths.raw = [resolve_path(p, pp) for p in profile.paths.raw]

    # Resolve single paths
    profile.paths.processed = resolve_path(profile.paths.processed, pp)
    profile.paths.export = resolve_path(profile.paths.export, pp)
    profile.passwords.passwords_file = resolve_path(profile.passwords.passwords_file, pp)

    # Gmail credentials (resolve explicit paths; defaults handled by get_gmail_config_paths())
    if profile.gmail.credentials_file:
        profile.gmail.credentials_file = resolve_path(profile.gmail.credentials_file, pp)

    if profile.gmail.token_file:
        profile.gmail.token_file = resolve_path(profile.gmail.token_file, pp)



def get_profiles_dir() -> Path:
    """Get profiles directory (PAPERTRAIL_PROFILES_DIR env var or repo profiles/)."""
    env_dir = os.environ.get("PAPERTRAIL_PROFILES_DIR")
    if env_dir:
        path = Path(env_dir).expanduser()
        if path.is_dir():
            return path
    module_dir = Path(__file__).parent
    repo_root = module_dir.parent
    return repo_root / "profiles"


def list_available_profiles() -> List[str]:
    """List all available profile names (subdirectories containing profile.yaml)."""
    profiles_dir = get_profiles_dir()
    if not profiles_dir.exists():
        return []
    return sorted([
        d.name for d in profiles_dir.iterdir()
        if d.is_dir() and (d / "profile.yaml").exists()
    ])


def load_profile(name: str) -> Profile:
    """Load a profile by name from the profiles directory."""
    if yaml is None:
        raise ProfileError("PyYAML is not installed.")

    profiles_dir = get_profiles_dir()
    profile_path = profiles_dir / name / "profile.yaml"

    if not profile_path.exists():
        available = list_available_profiles()
        raise ProfileNotFoundError(
            f"Profile '{name}' not found at {profile_path}. "
            f"Available: {', '.join(available) or 'none'}"
        )

    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ProfileParseError(f"Failed to parse profile '{name}': {e}")

    if not isinstance(data, dict):
        raise ProfileParseError(f"Profile '{name}' must be a YAML mapping")

    profile = _parse_profile_dict(data, profile_path)
    resolve_paths_in_profile(profile)
    return profile


def _default_reconciliation_rules() -> List[ReconciliationRule]:
    """Return default rules equivalent to the old hardcoded behavior."""
    return [
        ReconciliationRule(
            name="bank-fee",
            match_description=[
                "COMISSAO", "IMPOSTO SELO", "IMP.SELO", "JUROS",
                "ANUIDADE", "MANUTENCAO", "DESPESAS", "PORTES",
            ],
            required_types={"bank-note": 1},
        ),
        ReconciliationRule(
            name="default-credit",
            direction="credit",
            required_types={
                "bank-note|invoice-credit": 1,
            },
        ),
        ReconciliationRule(
            name="default-debit",
            direction="debit",
            required_types={
                "bank-note": 1,
                "invoice|receipt|invoice-receipt|invoice-credit|invoice-debit|invoice-order|receipt-reference|receipt-delivery": [1, None],
            },
        ),
    ]


def _parse_reconciliation_config(recon_data: Dict[str, Any]) -> ReconciliationConfig:
    """Parse reconciliation config from YAML data."""
    rules_data = recon_data.get("rules")
    if rules_data is None:
        # No rules configured — use defaults
        return ReconciliationConfig(rules=_default_reconciliation_rules())

    rules = []
    for rd in rules_data:
        if not isinstance(rd, dict) or "name" not in rd:
            continue
        rules.append(ReconciliationRule(
            name=rd["name"],
            match_description=rd.get("match_description", []),
            direction=rd.get("direction"),
            required_types=rd.get("required_types", {}),
            shared_types=rd.get("shared_types", {}),
        ))
    return ReconciliationConfig(rules=rules)


def _parse_profile_dict(data: Dict[str, Any], profile_path: Path) -> Profile:
    """Parse a profile dictionary into Profile dataclass."""
    if "profile" not in data:
        raise ProfileValidationError("Missing required field: profile")

    profile_meta_data = data.get("profile", {})
    if not isinstance(profile_meta_data, dict) or "name" not in profile_meta_data:
        raise ProfileValidationError("Missing required field: profile.name")

    profile_meta = ProfileMetadata(
        name=profile_meta_data["name"],
        description=profile_meta_data.get("description", ""),
        tax_number=profile_meta_data.get("tax_number"),
    )

    paths_data = data.get("paths", {})
    paths = PathsConfig(
        raw=paths_data.get("raw", []) if isinstance(paths_data.get("raw"), list) else [paths_data.get("raw")] if paths_data.get("raw") else [],
        processed=paths_data.get("processed"),
        export=paths_data.get("export")
    )

    openrouter_data = data.get("openrouter", {})
    openrouter = OpenRouterConfig(
        model_id=openrouter_data.get("model_id"),
        api_key=openrouter_data.get("api_key"),
        base_url=openrouter_data.get("base_url") or "https://openrouter.ai/api/v1"
    )

    doc_types_data = data.get("document_types", {})
    document_types = DocumentTypesConfig(
        predefined=doc_types_data.get("predefined") or doc_types_data.get("canonical"),
    )

    gmail_data = data.get("gmail", {})
    gmail_settings = gmail_data.get("settings", {})
    gmail = GmailConfig(
        enabled=gmail_data.get("enabled", False),
        credentials_file=gmail_data.get("credentials_file"),
        token_file=gmail_data.get("token_file"),
        attachment_mime_types=gmail_settings.get("attachment_mime_types", ["application/pdf"]),
        label_filter=gmail_settings.get("label_filter"),
        max_results_per_query=gmail_settings.get("max_results_per_query", 500),
        skip_already_downloaded=gmail_settings.get("skip_already_downloaded", True)
    )

    passwords_data = data.get("passwords", {})
    passwords = PasswordsConfig(
        passwords=passwords_data.get("passwords"),
        passwords_file=passwords_data.get("passwords_file")
    )

    pipeline_data = data.get("pipeline", {})
    pipeline = PipelineConfig(
        tools_required=pipeline_data.get("tools_required", []),
        default_export_date=pipeline_data.get("default_export_date", "last_month")
    )

    nif_api_data = data.get("nif_api", {})
    nif_api = NifApiConfig(
        enabled=nif_api_data.get("enabled", False),
    )

    recon_data = data.get("reconciliation", {})
    reconciliation = _parse_reconciliation_config(recon_data)

    export_data = data.get("export", {})
    fm_data = export_data.get("file_mappings", {})
    export_rules = []
    for rule_data in fm_data.get("rules", []):
        if isinstance(rule_data, dict) and "match" in rule_data and "prefix" in rule_data:
            export_rules.append(ExportMappingRule(
                match=rule_data["match"],
                prefix=rule_data["prefix"],
            ))
    export_file_mappings = ExportFileMappingsConfig(
        enabled=fm_data.get("enabled", False),
        default_prefix=fm_data.get("default_prefix", ""),
        filename_fields=fm_data.get("filename_fields"),
        rules=export_rules,
    )
    export_config = ExportConfig(
        max_file_size_mb=export_data.get("max_file_size_mb"),
        file_mappings=export_file_mappings,
    )

    return Profile(
        profile=profile_meta,
        paths=paths,
        openrouter=openrouter,
        document_types=document_types,
        gmail=gmail,
        passwords=passwords,
        pipeline=pipeline,
        nif_api=nif_api,
        reconciliation=reconciliation,
        export=export_config,
        task_defaults=data.get("task_defaults", {}),
        _profile_path=profile_path
    )



def get_passwords_from_profile(profile: Profile) -> tuple[list[str], str | None]:
    """Get passwords list from profile."""
    if profile.passwords.passwords:
        return (profile.passwords.passwords, None)

    if profile.passwords.passwords_file:
        passwords_file = Path(profile.passwords.passwords_file)
        if passwords_file.exists():
            with open(passwords_file, 'r', encoding='utf-8') as f:
                passwords = [line.strip() for line in f if line.strip()]
            return (passwords, str(passwords_file))

    return ([], None)


