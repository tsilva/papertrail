"""Configuration, profile loading, and environment management."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import openai

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


# --- Exceptions ---

class ConfigError(Exception):
    """Base exception for configuration errors."""

class ProfileNotFoundError(ConfigError):
    """Raised when a requested profile cannot be found."""

class ProfileParseError(ConfigError):
    """Raised when a profile file cannot be parsed."""


# --- Config dict wrapper ---

class Config:
    """YAML config with dot-path access.

    Nested dicts become Config objects. Missing keys return None.
    Also supports dict-like methods (items, keys, etc.) for data dicts.
    """

    def __init__(self, data: dict):
        object.__setattr__(self, '_data', data)

    def __getattr__(self, key):
        try:
            val = self._data[key]
        except KeyError:
            return None
        if isinstance(val, dict):
            return Config(val)
        if isinstance(val, list):
            return [Config(item) if isinstance(item, dict) else item for item in val]
        return val

    def __setattr__(self, key, value):
        self._data[key] = value

    def get(self, key, default=None):
        val = self._data.get(key)
        if val is None:
            return default
        if isinstance(val, dict):
            return Config(val)
        if isinstance(val, list):
            return [Config(item) if isinstance(item, dict) else item for item in val]
        return val

    def items(self):
        return self._data.items()

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        return key in self._data

    def __bool__(self):
        return bool(self._data)


# Backwards compatibility
Profile = Config


# --- Profile loading ---

def _resolve_path(path_str, profile_path):
    """Resolve a path string relative to the profile file location."""
    if not path_str:
        return None
    path = Path(path_str)
    if path.is_absolute():
        return str(path)
    return str((profile_path.parent / path).resolve())


_DEFAULT_RECON_RULES = [
    {
        "name": "bank-fee",
        "match_description": [
            "COMISSAO", "IMPOSTO SELO", "IMP.SELO", "JUROS",
            "ANUIDADE", "MANUTENCAO", "DESPESAS", "PORTES",
        ],
        "required_types": {"bank-note": 1},
    },
    {
        "name": "default-credit",
        "direction": "credit",
        "required_types": {"bank-note|invoice-credit": 1},
    },
    {
        "name": "default-debit",
        "direction": "debit",
        "required_types": {
            "bank-note": 1,
            "invoice|receipt|invoice-receipt|invoice-credit|invoice-debit|invoice-order|receipt-reference|receipt-delivery": [1, None],
        },
    },
]


def _normalize_data(data, profile_path):
    """Apply defaults, flatten nested structures, resolve paths."""
    # Top-level section defaults
    defaults = {"profile": {}, "paths": {}, "openrouter": {}, "gmail": {},
                "passwords": {}, "nif_api": {}, "reconciliation": {}, "export": {}}
    for k, v in defaults.items():
        data.setdefault(k, v)

    data["profile"].setdefault("description", "")

    # Flatten gmail.settings into gmail
    gmail = data["gmail"]
    settings = gmail.pop("settings", {})
    for k, v in settings.items():
        gmail.setdefault(k, v)

    # Scalar defaults
    data["openrouter"].setdefault("base_url", "https://openrouter.ai/api/v1")
    gmail.setdefault("enabled", False)
    gmail.setdefault("attachment_mime_types", ["application/pdf"])
    gmail.setdefault("max_results_per_query", 500)
    gmail.setdefault("skip_already_downloaded", True)
    data["nif_api"].setdefault("enabled", False)

    # Normalize paths.raw to list
    paths = data["paths"]
    raw = paths.get("raw")
    if isinstance(raw, str):
        paths["raw"] = [raw]
    elif raw is None:
        paths["raw"] = []

    # Reconciliation defaults
    recon = data["reconciliation"]
    if "rules" not in recon:
        recon["rules"] = [dict(r) for r in _DEFAULT_RECON_RULES]
    else:
        recon["rules"] = [r for r in recon["rules"] if isinstance(r, dict) and "name" in r]
    recon.setdefault("exclude_prefixes", [])
    for rule in recon["rules"]:
        rule.setdefault("match_description", [])
        rule.setdefault("required_types", {})
        rule.setdefault("shared_types", {})
        rule.setdefault("companions", [])
        rule.setdefault("expected_page_count", {})

    # Export defaults
    export = data["export"]
    export.setdefault("file_mappings", {})
    export["file_mappings"].setdefault("enabled", False)
    export["file_mappings"].setdefault("default_prefix", "")
    export["file_mappings"].setdefault("rules", [])
    export.setdefault("merge_rules", [])

    # Profile path metadata
    data["_profile_path"] = profile_path
    data["profile_dir"] = profile_path.parent if profile_path else None

    # Resolve relative paths
    if profile_path:
        if paths.get("raw"):
            paths["raw"] = [_resolve_path(p, profile_path) for p in paths["raw"]]
        paths["processed"] = _resolve_path(paths.get("processed"), profile_path)
        paths["export"] = _resolve_path(paths.get("export"), profile_path)
        data["passwords"]["passwords_file"] = _resolve_path(
            data["passwords"].get("passwords_file"), profile_path
        )
        if gmail.get("credentials_file"):
            gmail["credentials_file"] = _resolve_path(gmail["credentials_file"], profile_path)
        if gmail.get("token_file"):
            gmail["token_file"] = _resolve_path(gmail["token_file"], profile_path)


def get_profiles_dir() -> Path:
    """Get profiles directory (PAPERTRAIL_PROFILES_DIR env var or repo profiles/)."""
    env_dir = os.environ.get("PAPERTRAIL_PROFILES_DIR")
    if env_dir:
        path = Path(env_dir).expanduser()
        if path.is_dir():
            return path
    return Path(__file__).parent.parent / "profiles"


def list_available_profiles():
    """List all available profile names."""
    profiles_dir = get_profiles_dir()
    if not profiles_dir.exists():
        return []
    return sorted([
        d.name for d in profiles_dir.iterdir()
        if d.is_dir() and (d / "profile.yaml").exists()
    ])


def load_profile(name):
    """Load a profile by name from the profiles directory."""
    if yaml is None:
        raise ConfigError("PyYAML is not installed.")

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
    if "profile" not in data or not isinstance(data.get("profile"), dict):
        raise ConfigError("Missing required field: profile")
    if "name" not in data["profile"]:
        raise ConfigError("Missing required field: profile.name")

    _normalize_data(data, profile_path)
    return Config(data)


def get_passwords_from_profile(profile):
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


# --- Runtime state ---

_current_profile: Optional[Config] = None


@dataclass
class AppContext:
    """Runtime application context holding all initialized resources."""
    model_id: str
    openai_client: any
    nif_cache: any  # NIFLookupCache or None


_ctx: AppContext | None = None


def get_ctx() -> AppContext:
    """Get the application context. Raises if not initialized."""
    if _ctx is None:
        raise RuntimeError("Application context not initialized. Call initialize_config() first.")
    return _ctx


def set_ctx(ctx: AppContext) -> None:
    global _ctx
    _ctx = ctx


def set_current_profile(profile: Optional[Config]) -> None:
    global _current_profile
    _current_profile = profile


def get_current_profile() -> Optional[Config]:
    return _current_profile


def get_repo_root() -> Path:
    return Path(__file__).parent.parent


def get_cache_dir() -> Path:
    cache_dir = get_repo_root() / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_gmail_config_paths() -> dict[str, Path]:
    """Get paths to Gmail API configuration files."""
    profile = get_current_profile()
    credentials_dir = get_repo_root() / ".credentials"
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


def get_openai_client() -> openai.OpenAI:
    """Get a configured OpenAI client for OpenRouter."""
    profile = get_current_profile()
    if not profile:
        raise RuntimeError("No profile is active. Use --profile to specify a configuration profile.")
    api_key = profile.openrouter.api_key
    base_url = profile.openrouter.base_url
    if not api_key:
        raise RuntimeError(
            f"No OpenRouter API key configured in profile '{profile.profile.name}'. "
            "Set openrouter.api_key in your profile YAML."
        )
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def get_passwords() -> tuple[list[str], str | None]:
    """Get passwords for ZIP extraction from profile."""
    profile = get_current_profile()
    if profile:
        return get_passwords_from_profile(profile)
    return ([], None)


def check_api_accessibility(base_url: str, timeout: int = 10) -> bool:
    """Check if the API base URL is accessible."""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(base_url, method='HEAD')
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError):
        return False
