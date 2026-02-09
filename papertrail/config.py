"""Configuration loading and environment management."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import openai

from papertrail.profiles import Profile


_current_profile: Optional[Profile] = None


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
    """Set the application context."""
    global _ctx
    _ctx = ctx


def set_current_profile(profile: Optional[Profile]) -> None:
    """Set the current active profile."""
    global _current_profile
    _current_profile = profile


def get_current_profile() -> Optional[Profile]:
    """Get the current active profile, or None if not set."""
    return _current_profile


def get_repo_root() -> Path:
    """Get the repository root directory."""
    return Path(__file__).parent.parent


def get_cache_dir() -> Path:
    """Get the cache directory path, creating it if needed."""
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
        raise RuntimeError(
            "No profile is active. Use --profile to specify a configuration profile."
        )

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
    from papertrail.profiles import get_passwords_from_profile

    profile = get_current_profile()

    if profile:
        return get_passwords_from_profile(profile)

    return ([], None)


def get_validations() -> tuple[dict, str | None]:
    """Get file validation rules from profile."""
    from papertrail.profiles import get_validations_from_profile

    profile = get_current_profile()
    if profile:
        return get_validations_from_profile(profile)
    return ({}, None)


def resolve_validations_file() -> tuple[str | None, str | None]:
    """Resolve validation rules to a file path, creating temp file if needed."""
    import json
    import tempfile

    validations, validations_file = get_validations()
    if not validations or not validations.get('rules'):
        return None, None

    if validations_file:
        return validations_file, None

    temp = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
    json.dump(validations['rules'], temp, indent=2)
    temp.close()
    return temp.name, temp.name


def check_api_accessibility(base_url: str, timeout: int = 10) -> bool:
    """Check if the API base URL is accessible."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(base_url, method='HEAD')
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        # HTTP error responses (4xx, 5xx) still mean server is accessible
        return True
    except (urllib.error.URLError, TimeoutError):
        return False
