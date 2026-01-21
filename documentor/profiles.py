"""
Profile-based configuration system for Documentor.

Provides YAML-based configuration profiles for managing multiple environments
(personal, work, testing) with a single file per environment.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


# ============================================================================
# Exceptions
# ============================================================================


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


class EnvironmentVariableError(ProfileError):
    """Raised when a referenced environment variable is undefined."""
    pass


# ============================================================================
# Data Models
# ============================================================================


@dataclass
class ProfileMetadata:
    """Profile metadata (name, description)."""
    name: str
    description: str = ""


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
    predefined: Optional[List[str]] = None  # null = dynamic loading
    fallback_file: Optional[str] = None
    fallback_list: Optional[List[str]] = None


@dataclass
class IssuingPartiesConfig:
    """Issuing parties configuration."""
    predefined: Optional[List[str]] = None  # null = dynamic loading
    fallback_list: Optional[List[str]] = None


@dataclass
class GmailSettingsConfig:
    """Gmail downloader settings."""
    attachment_mime_types: List[str] = field(default_factory=lambda: ["application/pdf"])
    label_filter: Optional[str] = None
    max_results_per_query: int = 500
    skip_already_downloaded: bool = True


@dataclass
class GmailConfig:
    """Gmail integration configuration."""
    enabled: bool = False
    credentials_file: Optional[str] = None  # Defaults to ../.credentials/gmail_credentials.json
    token_file: Optional[str] = None  # Defaults to ../.credentials/gmail_token.json
    settings: GmailSettingsConfig = field(default_factory=GmailSettingsConfig)


@dataclass
class PasswordsConfig:
    """Password configuration for ZIP extraction."""
    # Inline list of passwords (recommended)
    passwords: Optional[List[str]] = None
    # Or path to passwords file (legacy)
    passwords_file: Optional[str] = None


@dataclass
class ValidationsConfig:
    """File validation schema configuration."""
    # Inline validation rules (recommended)
    rules: Optional[List[Dict[str, Any]]] = None
    # Or path to validations JSON file (legacy)
    validations_file: Optional[str] = None


@dataclass
class PipelineConfig:
    """Pipeline task configuration."""
    tools_required: List[str] = field(default_factory=list)
    default_export_date: str = "last_month"


@dataclass
class Profile:
    """Complete profile configuration."""
    profile: ProfileMetadata
    paths: PathsConfig = field(default_factory=PathsConfig)
    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    document_types: DocumentTypesConfig = field(default_factory=DocumentTypesConfig)
    issuing_parties: IssuingPartiesConfig = field(default_factory=IssuingPartiesConfig)
    gmail: GmailConfig = field(default_factory=GmailConfig)
    passwords: PasswordsConfig = field(default_factory=PasswordsConfig)
    validations: ValidationsConfig = field(default_factory=ValidationsConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    task_defaults: Dict[str, Any] = field(default_factory=dict)

    # Store the profile file path for resolving relative paths
    _profile_path: Optional[Path] = field(default=None, repr=False)


# ============================================================================
# Environment Variable Expansion
# ============================================================================


def expand_env_vars(value: Any) -> Any:
    """
    Recursively expand environment variables in strings.

    Supports ${VAR} syntax. Raises EnvironmentVariableError if variable is undefined.

    Args:
        value: Any value (string, list, dict, etc.)

    Returns:
        Value with environment variables expanded

    Raises:
        EnvironmentVariableError: If a referenced variable is undefined
    """
    if isinstance(value, str):
        # Find all ${VAR} patterns
        pattern = r'\$\{([^}]+)\}'
        matches = re.findall(pattern, value)

        for var_name in matches:
            if var_name not in os.environ:
                raise EnvironmentVariableError(
                    f"Environment variable '{var_name}' is not defined. "
                    f"Set it in your .env file or environment before using this profile."
                )
            env_value = os.environ[var_name]
            value = value.replace(f'${{{var_name}}}', env_value)

        return value

    elif isinstance(value, list):
        return [expand_env_vars(item) for item in value]

    elif isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}

    else:
        return value


# ============================================================================
# Path Resolution
# ============================================================================


def resolve_path(path_str: Optional[str], profile_path: Path) -> Optional[Path]:
    """
    Resolve a path string relative to the profile file location.

    Args:
        path_str: Path string (can be relative or absolute)
        profile_path: Path to the profile file

    Returns:
        Resolved absolute Path, or None if path_str is None
    """
    if path_str is None:
        return None

    path = Path(path_str)

    # If absolute, return as-is
    if path.is_absolute():
        return path

    # Resolve relative to profile directory
    profile_dir = profile_path.parent
    return (profile_dir / path).resolve()


def _resolve_path_attr(obj: Any, attr: str, profile_path: Path) -> None:
    """Resolve a path attribute on an object in-place."""
    value = getattr(obj, attr, None)
    if value:
        resolved = resolve_path(value, profile_path)
        setattr(obj, attr, str(resolved) if resolved else None)


def resolve_paths_in_profile(profile: Profile) -> None:
    """
    Resolve all relative paths in a profile to absolute paths.

    Modifies the profile in-place.

    Args:
        profile: Profile to resolve paths in
    """
    if profile._profile_path is None:
        return

    pp = profile._profile_path

    # Resolve paths.raw (list of paths)
    if profile.paths.raw:
        profile.paths.raw = [str(resolve_path(p, pp)) for p in profile.paths.raw]

    # Resolve single path attributes
    _resolve_path_attr(profile.paths, "processed", pp)
    _resolve_path_attr(profile.paths, "export", pp)
    _resolve_path_attr(profile.document_types, "fallback_file", pp)
    _resolve_path_attr(profile.passwords, "passwords_file", pp)
    _resolve_path_attr(profile.validations, "validations_file", pp)

    # Resolve gmail config files (or set defaults if enabled)
    if profile.gmail.credentials_file:
        _resolve_path_attr(profile.gmail, "credentials_file", pp)
    elif profile.gmail.enabled:
        default_path = resolve_path("../.credentials/gmail_credentials.json", pp)
        profile.gmail.credentials_file = str(default_path) if default_path else None

    if profile.gmail.token_file:
        _resolve_path_attr(profile.gmail, "token_file", pp)
    elif profile.gmail.enabled:
        default_path = resolve_path("../.credentials/gmail_token.json", pp)
        profile.gmail.token_file = str(default_path) if default_path else None


# ============================================================================
# Profile Loading
# ============================================================================


def get_profiles_dir() -> Path:
    """Get the profiles directory path."""
    # Profiles directory is at repo root / profiles
    # This module is at repo root / documentor / profiles.py
    module_dir = Path(__file__).parent
    repo_root = module_dir.parent
    return repo_root / "profiles"


def list_available_profiles() -> List[str]:
    """
    List all available profile names (without .yaml extension).

    Returns:
        List of profile names
    """
    profiles_dir = get_profiles_dir()

    if not profiles_dir.exists():
        return []

    profiles = []
    for file_path in profiles_dir.glob("*.yaml"):
        # Skip .example files
        if file_path.stem.endswith(".example"):
            continue
        profiles.append(file_path.stem)

    return sorted(profiles)


def get_default_profile_name() -> str:
    """
    Get the default profile name.

    Returns:
        "default" if profiles/default.yaml exists, otherwise first available profile

    Raises:
        ProfileNotFoundError: If no profiles exist
    """
    available = list_available_profiles()

    if not available:
        raise ProfileNotFoundError(
            "No profiles found. Create a profile from profiles/*.yaml.example "
            "or use legacy .env configuration."
        )

    if "default" in available:
        return "default"

    return available[0]


def load_profile(name: str) -> Profile:
    """
    Load a profile by name from the profiles directory.

    Args:
        name: Profile name (without .yaml extension)

    Returns:
        Loaded and validated Profile

    Raises:
        ProfileNotFoundError: If profile file doesn't exist
        ProfileParseError: If YAML parsing fails
        ProfileValidationError: If required fields are missing
        EnvironmentVariableError: If referenced env vars are undefined
    """
    if yaml is None:
        raise ProfileError(
            "PyYAML is not installed. Install it with: pip install pyyaml"
        )

    profiles_dir = get_profiles_dir()
    profile_path = profiles_dir / f"{name}.yaml"

    # Check if profile exists
    if not profile_path.exists():
        available = list_available_profiles()
        available_str = ", ".join(available) if available else "none"
        raise ProfileNotFoundError(
            f"Profile '{name}' not found at {profile_path}\n"
            f"Available profiles: {available_str}\n"
            f"Create a profile from profiles/*.yaml.example or use --profile with an existing profile."
        )

    # Load YAML
    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ProfileParseError(
            f"Failed to parse profile '{name}'\n"
            f"  File: {profile_path}\n"
            f"  Error: {e}"
        )
    except Exception as e:
        raise ProfileParseError(
            f"Failed to read profile '{name}'\n"
            f"  File: {profile_path}\n"
            f"  Error: {e}"
        )

    if not isinstance(data, dict):
        raise ProfileParseError(
            f"Profile '{name}' must be a YAML mapping (got {type(data).__name__})"
        )

    # Expand environment variables
    try:
        data = expand_env_vars(data)
    except EnvironmentVariableError as e:
        raise EnvironmentVariableError(
            f"Profile '{name}' references undefined variable:\n  {e}"
        )

    # Parse into dataclasses
    try:
        profile = _parse_profile_dict(data, profile_path)
    except Exception as e:
        raise ProfileValidationError(
            f"Failed to validate profile '{name}'\n"
            f"  File: {profile_path}\n"
            f"  Error: {e}\n"
            f"Check profiles/README.md for schema documentation."
        )

    # Resolve relative paths
    resolve_paths_in_profile(profile)

    return profile


def _parse_profile_dict(data: Dict[str, Any], profile_path: Path) -> Profile:
    """
    Parse a profile dictionary into Profile dataclass.

    Args:
        data: Raw profile data from YAML
        profile_path: Path to the profile file

    Returns:
        Parsed Profile

    Raises:
        ProfileValidationError: If required fields are missing
    """
    # Parse profile metadata
    if "profile" not in data:
        raise ProfileValidationError("Missing required field: profile")

    profile_meta_data = data.get("profile", {})
    if not isinstance(profile_meta_data, dict):
        raise ProfileValidationError("Field 'profile' must be a mapping")

    if "name" not in profile_meta_data:
        raise ProfileValidationError("Missing required field: profile.name")

    profile_meta = ProfileMetadata(
        name=profile_meta_data["name"],
        description=profile_meta_data.get("description", "")
    )

    # Parse paths
    paths_data = data.get("paths", {})
    paths = PathsConfig(
        raw=paths_data.get("raw", []),
        processed=paths_data.get("processed"),
        export=paths_data.get("export")
    )

    # Ensure raw is a list
    if isinstance(paths.raw, str):
        paths.raw = [paths.raw]

    # Parse openrouter
    openrouter_data = data.get("openrouter", {})
    openrouter = OpenRouterConfig(
        model_id=openrouter_data.get("model_id"),
        api_key=openrouter_data.get("api_key"),
        base_url=openrouter_data.get("base_url", "https://openrouter.ai/api/v1")
    )

    # Parse document_types (support both "predefined" and "canonical" field names)
    doc_types_data = data.get("document_types", {})
    document_types = DocumentTypesConfig(
        predefined=doc_types_data.get("predefined") or doc_types_data.get("canonical"),
        fallback_file=doc_types_data.get("fallback_file"),
        fallback_list=doc_types_data.get("fallback_list")
    )

    # Parse issuing_parties (support both "predefined" and "canonical" field names)
    issuing_data = data.get("issuing_parties", {})
    issuing_parties = IssuingPartiesConfig(
        predefined=issuing_data.get("predefined") or issuing_data.get("canonical"),
        fallback_list=issuing_data.get("fallback_list")
    )

    # Parse gmail
    gmail_data = data.get("gmail", {})
    gmail_settings_data = gmail_data.get("settings", {})
    gmail_settings = GmailSettingsConfig(
        attachment_mime_types=gmail_settings_data.get("attachment_mime_types", ["application/pdf"]),
        label_filter=gmail_settings_data.get("label_filter"),
        max_results_per_query=gmail_settings_data.get("max_results_per_query", 500),
        skip_already_downloaded=gmail_settings_data.get("skip_already_downloaded", True)
    )
    gmail = GmailConfig(
        enabled=gmail_data.get("enabled", False),
        credentials_file=gmail_data.get("credentials_file"),
        token_file=gmail_data.get("token_file"),
        settings=gmail_settings
    )

    # Parse passwords
    passwords_data = data.get("passwords", {})
    passwords = PasswordsConfig(
        passwords=passwords_data.get("passwords"),  # Inline list
        passwords_file=passwords_data.get("passwords_file")  # Or file path
    )

    # Parse validations
    validations_data = data.get("validations", {})
    validations = ValidationsConfig(
        rules=validations_data.get("rules"),  # Inline rules
        validations_file=validations_data.get("validations_file")  # Or file path
    )

    # Parse pipeline
    pipeline_data = data.get("pipeline", {})
    pipeline = PipelineConfig(
        tools_required=pipeline_data.get("tools_required", []),
        default_export_date=pipeline_data.get("default_export_date", "last_month")
    )

    # Parse task_defaults
    task_defaults = data.get("task_defaults", {})

    return Profile(
        profile=profile_meta,
        paths=paths,
        openrouter=openrouter,
        document_types=document_types,
        issuing_parties=issuing_parties,
        gmail=gmail,
        passwords=passwords,
        validations=validations,
        pipeline=pipeline,
        task_defaults=task_defaults,
        _profile_path=profile_path
    )


# ============================================================================
# Helper Functions
# ============================================================================


def get_passwords_from_profile(profile: Profile) -> tuple[list[str], str | None]:
    """
    Get passwords list from profile.

    Checks inline passwords first, then falls back to passwords file if specified.

    Args:
        profile: Profile to get passwords from

    Returns:
        Tuple of (passwords_list, file_path_or_none):
        - If using inline passwords: (passwords, None)
        - If using file reference: (passwords, file_path)
        - If no passwords configured: ([], None)
    """
    # Check inline passwords first
    if profile.passwords.passwords:
        return (profile.passwords.passwords, None)

    # Fall back to passwords file
    if profile.passwords.passwords_file:
        passwords_file = Path(profile.passwords.passwords_file)
        if passwords_file.exists():
            with open(passwords_file, 'r', encoding='utf-8') as f:
                passwords = [line.strip() for line in f if line.strip()]
            return (passwords, str(passwords_file))

    return ([], None)


def get_validations_from_profile(profile: Profile) -> tuple[dict, str | None]:
    """
    Get validation rules from profile.

    Checks inline rules first, then falls back to validations file if specified.

    Args:
        profile: Profile to get validations from

    Returns:
        Tuple of (validations_dict, file_path_or_none):
        - If using inline rules: ({"rules": rules}, None)
        - If using file reference: (validations_dict, file_path)
        - If no validations configured: ({}, None)
    """
    import json

    # Check inline rules first
    if profile.validations.rules:
        return ({"rules": profile.validations.rules}, None)

    # Fall back to validations file
    if profile.validations.validations_file:
        validations_file = Path(profile.validations.validations_file)
        if validations_file.exists():
            with open(validations_file, 'r', encoding='utf-8') as f:
                validations = json.load(f)
            return (validations, str(validations_file))

    return ({}, None)
