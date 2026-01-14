"""Configuration loading and environment management."""

import os
from pathlib import Path
from typing import Optional

import openai
from dotenv import load_dotenv


def get_repo_root() -> Path:
    """Get the repository root directory."""
    return Path(__file__).parent.parent


def get_config_paths() -> dict[str, Path]:
    """Get paths to all configuration files."""
    repo_root = get_repo_root()
    config_dir = repo_root / "config"
    return {
        "env": repo_root / ".env",  # .env stays at repo root
        "passwords": config_dir / "passwords.txt",
        "validations": config_dir / "file_check_validations.json",
        "document_types": config_dir / "document_types.json",
    }


def get_gmail_config_paths() -> dict[str, Path]:
    """Get paths to Gmail API configuration files."""
    config_dir = get_repo_root() / "config"
    return {
        "credentials": config_dir / "gmail_credentials.json",
        "token": config_dir / "gmail_token.json",
        "settings": config_dir / "gmail_settings.json",
    }


def load_env() -> Path:
    """
    Load environment variables from repo root .env file.

    Returns:
        Path to the .env file
    """
    paths = get_config_paths()
    env_path = paths["env"]

    if not env_path.exists():
        raise FileNotFoundError(
            f"No .env file found at {env_path}. "
            "Copy .env.example to .env and configure it."
        )

    load_dotenv(dotenv_path=env_path, override=True)
    return env_path


def load_config() -> dict:
    """
    Load configuration from repo root .env.

    Returns:
        Dictionary with configuration values
    """
    env_path = load_env()
    paths = get_config_paths()

    return {
        "repo_root": get_repo_root(),
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
        api_key: Optional API key (defaults to OPENROUTER_API_KEY env var)
        base_url: Optional base URL (defaults to OPENROUTER_BASE_URL env var)

    Returns:
        Configured OpenAI client
    """
    if api_key is None:
        api_key = os.getenv("OPENROUTER_API_KEY")
    if base_url is None:
        base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    return openai.OpenAI(api_key=api_key, base_url=base_url)
