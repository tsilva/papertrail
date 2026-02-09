"""Configuration loading and environment management."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import openai

from papertrail.profiles import Profile


# ============================================================================
# Profile State Management
# ============================================================================

# Module-level variable to store the current active profile
_current_profile: Optional[Profile] = None


# ============================================================================
# Application Context
# ============================================================================


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
    """
    Set the current active profile.

    Args:
        profile: Profile to set as active
    """
    global _current_profile
    _current_profile = profile


def get_current_profile() -> Optional[Profile]:
    """
    Get the current active profile.

    Returns:
        Current profile, or None if not set
    """
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
    """
    Get paths to Gmail API configuration files.

    If a profile is active, uses paths from the profile.
    Otherwise, uses default credential directory paths.

    Returns:
        Dictionary with Gmail config file paths
    """
    profile = get_current_profile()
    credentials_dir = get_repo_root() / ".credentials"

    # Build base paths
    paths = {
        "credentials": credentials_dir / "gmail_credentials.json",
        "token": credentials_dir / "gmail_token.json",
        "settings": credentials_dir / "gmail_settings.json",
    }

    # Override with profile paths if available
    if profile and profile.gmail.enabled:
        if profile.gmail.credentials_file:
            paths["credentials"] = Path(profile.gmail.credentials_file)
        if profile.gmail.token_file:
            paths["token"] = Path(profile.gmail.token_file)
        # Note: settings are embedded in profile, not a separate file

    return paths


def get_openai_client() -> openai.OpenAI:
    """
    Get a configured OpenAI client for OpenRouter.

    Requires an active profile with OpenRouter configuration.

    Returns:
        Configured OpenAI client

    Raises:
        RuntimeError: If no profile is active
    """
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
    """
    Get passwords for ZIP extraction from profile.

    Returns:
        Tuple of (passwords_list, file_path_or_none):
        - If using inline passwords: (passwords, None)
        - If using file reference: (passwords, file_path)
        - If no passwords configured: ([], None)
    """
    from papertrail.profiles import get_passwords_from_profile

    profile = get_current_profile()

    if profile:
        return get_passwords_from_profile(profile)

    return ([], None)


def get_validations() -> tuple[dict, str | None]:
    """
    Get file validation rules from profile.

    Returns:
        Tuple of (validations_dict, file_path_or_none):
        - If using inline rules: ({"rules": rules}, None)
        - If using file reference: (validations_dict, file_path)
        - If no validations configured: ({}, None)
    """
    from papertrail.profiles import get_validations_from_profile

    profile = get_current_profile()

    if profile:
        return get_validations_from_profile(profile)

    return ({}, None)


def check_api_accessibility(base_url: str, timeout: int = 10) -> bool:
    """Check if the API base URL is accessible.

    Args:
        base_url: The API base URL to check.
        timeout: Connection timeout in seconds.

    Returns:
        True if the API is accessible, False otherwise.
    """
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
