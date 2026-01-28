"""Configuration loading and environment management."""

import os
from pathlib import Path
from typing import Optional

import openai
from dotenv import load_dotenv

from papertrail.profiles import Profile


# ============================================================================
# Profile State Management
# ============================================================================

# Module-level variable to store the current active profile
_current_profile: Optional[Profile] = None


def set_current_profile(profile: Optional[Profile]) -> None:
    """
    Set the current active profile.

    Args:
        profile: Profile to set as active, or None to use legacy .env mode
    """
    global _current_profile
    _current_profile = profile


def get_current_profile() -> Optional[Profile]:
    """
    Get the current active profile.

    Returns:
        Current profile, or None if using legacy .env mode
    """
    return _current_profile


def get_repo_root() -> Path:
    """Get the repository root directory."""
    return Path(__file__).parent.parent


def get_config_paths() -> dict[str, Path]:
    """
    Get paths to all configuration files.

    If a profile is active, uses paths from the profile.
    Otherwise, falls back to legacy config directory paths.

    Returns:
        Dictionary with configuration file paths
    """
    profile = get_current_profile()
    repo_root = get_repo_root()
    config_dir = repo_root / "config"

    # Build base paths (legacy)
    paths = {
        "env": repo_root / ".env",  # .env stays at repo root
        "passwords": config_dir / "passwords.txt",
        "validations": config_dir / "file_check_validations.json",
        "document_types": config_dir / "document_types.json",
    }

    # Override with profile paths if available
    if profile:
        # Passwords: use file path if specified (for legacy support)
        if profile.passwords.passwords_file:
            paths["passwords"] = Path(profile.passwords.passwords_file)

        # Validations: use file path if specified (for legacy support)
        if profile.validations.validations_file:
            paths["validations"] = Path(profile.validations.validations_file)

        # Document types fallback file
        if profile.document_types.fallback_file:
            paths["document_types"] = Path(profile.document_types.fallback_file)

    return paths


def get_gmail_config_paths() -> dict[str, Path]:
    """
    Get paths to Gmail API configuration files.

    If a profile is active, uses paths from the profile.
    Otherwise, falls back to legacy config directory paths.

    Returns:
        Dictionary with Gmail config file paths
    """
    profile = get_current_profile()
    config_dir = get_repo_root() / "config"

    # Build base paths (legacy)
    paths = {
        "credentials": config_dir / "gmail_credentials.json",
        "token": config_dir / "gmail_token.json",
        "settings": config_dir / "gmail_settings.json",
    }

    # Override with profile paths if available
    if profile and profile.gmail.enabled:
        if profile.gmail.credentials_file:
            paths["credentials"] = Path(profile.gmail.credentials_file)
        if profile.gmail.token_file:
            paths["token"] = Path(profile.gmail.token_file)
        # Note: settings are embedded in profile, not a separate file

    return paths


def load_env(env_name: Optional[str] = None) -> tuple[Optional[Path], Optional[str]]:
    """
    Load environment variables from .env, or .env.{name} if specified.

    Used for environment variable expansion in profile values (e.g., ${OPENROUTER_API_KEY}).
    When env_name is provided, loads .env.{name} instead of .env (standalone behavior).

    Args:
        env_name: Optional environment name (loads .env.{name} instead of .env)

    Returns:
        Tuple of (env_path, env_name) - env_path is the file that was loaded
    """
    import logging

    repo_root = get_repo_root()

    # Load environment file (standalone behavior)
    if env_name:
        # Only load .env.{name} when specified
        env_path = repo_root / f".env.{env_name}"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=True)
        else:
            logging.getLogger('cli').warning(f"Environment file not found: {env_path}")
            env_path = None
    else:
        # Load base .env when no env specified
        env_path = repo_root / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=True)
        else:
            env_path = None

    return (env_path, env_name)


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
