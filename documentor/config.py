"""Configuration loading and environment management."""

import os
from pathlib import Path
from typing import Optional

import openai
from dotenv import load_dotenv

from documentor.profiles import Profile


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


def load_env(required: bool = True) -> Optional[Path]:
    """
    Load environment variables from repo root .env file.

    Args:
        required: If True, raises error if .env not found. If False, silently returns None.

    Returns:
        Path to the .env file, or None if not found and not required

    Raises:
        FileNotFoundError: If .env not found and required=True
    """
    paths = get_config_paths()
    env_path = paths["env"]

    if not env_path.exists():
        if required:
            raise FileNotFoundError(
                f"No .env file found at {env_path}. "
                "Copy .env.example to .env and configure it, "
                "or use a profile with --profile."
            )
        return None

    load_dotenv(dotenv_path=env_path, override=True)
    return env_path


def load_config() -> dict:
    """
    Load configuration from active profile or legacy .env.

    If a profile is active, uses values from the profile.
    Otherwise, loads from .env file (legacy mode).

    Returns:
        Dictionary with configuration values
    """
    profile = get_current_profile()
    repo_root = get_repo_root()
    paths = get_config_paths()

    # Profile mode
    if profile:
        # Load .env for environment variable expansion (not required)
        env_path = load_env(required=False)

        # Build config from profile
        return {
            "repo_root": repo_root,
            "env_path": env_path,
            "passwords_path": paths["passwords"],
            "validations_path": paths["validations"],
            "OPENROUTER_MODEL_ID": profile.openrouter.model_id,
            "OPENROUTER_API_KEY": profile.openrouter.api_key,
            "OPENROUTER_BASE_URL": profile.openrouter.base_url,
            "RAW_FILES_DIR": ";".join(profile.paths.raw) if profile.paths.raw else None,
            "PROCESSED_FILES_DIR": profile.paths.processed,
            "EXPORT_FILES_DIR": profile.paths.export,
        }

    # Legacy .env mode
    env_path = load_env(required=True)

    return {
        "repo_root": repo_root,
        "env_path": env_path,
        "passwords_path": paths["passwords"],
        "validations_path": paths["validations"],
        "OPENROUTER_MODEL_ID": os.getenv("OPENROUTER_MODEL_ID"),
        "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY"),
        "OPENROUTER_BASE_URL": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        "RAW_FILES_DIR": os.getenv("RAW_FILES_DIR"),
        "PROCESSED_FILES_DIR": os.getenv("PROCESSED_FILES_DIR"),
        "EXPORT_FILES_DIR": os.getenv("EXPORT_FILES_DIR"),
    }


def get_openai_client(api_key: Optional[str] = None, base_url: Optional[str] = None) -> openai.OpenAI:
    """
    Get a configured OpenAI client for OpenRouter.

    Args:
        api_key: Optional API key (defaults to profile or OPENROUTER_API_KEY env var)
        base_url: Optional base URL (defaults to profile or OPENROUTER_BASE_URL env var)

    Returns:
        Configured OpenAI client
    """
    profile = get_current_profile()

    # Use profile values if available and not overridden
    if api_key is None:
        if profile and profile.openrouter.api_key:
            api_key = profile.openrouter.api_key
        else:
            api_key = os.getenv("OPENROUTER_API_KEY")

    if base_url is None:
        if profile and profile.openrouter.base_url:
            base_url = profile.openrouter.base_url
        else:
            base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

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
    from documentor.profiles import get_passwords_from_profile

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
    from documentor.profiles import get_validations_from_profile

    profile = get_current_profile()

    if profile:
        return get_validations_from_profile(profile)

    return ({}, None)
