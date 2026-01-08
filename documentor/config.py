"""Configuration loading and environment management."""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import openai
from dotenv import load_dotenv


def get_config_dir_and_env_path() -> tuple[Path, Path]:
    """Get the configuration directory and .env file path."""
    config_dir = Path.home() / ".documentor"
    env_path = config_dir / ".env"
    return config_dir, env_path


def ensure_home_config_and_env() -> tuple[Path, Path]:
    """
    Ensure the home config directory exists and contains required config files.

    Copies example config files if they don't exist, and creates .env if missing.
    Exits if files were copied (user needs to edit them first).

    Returns:
        Tuple of (config_dir, env_path)
    """
    config_dir, env_path = get_config_dir_and_env_path()
    config_dir.mkdir(parents=True, exist_ok=True)

    # Find config example directory (relative to main.py location)
    # This handles both installed package and development scenarios
    possible_config_dirs = [
        Path(__file__).parent.parent / "config",  # Development: documentor/../config
        Path(__file__).parent / "config",  # Installed: documentor/config
    ]

    config_example_dir = None
    for d in possible_config_dirs:
        if d.exists():
            config_example_dir = d
            break

    files_copied = []
    if config_example_dir:
        for file in config_example_dir.iterdir():
            if file.is_file() and file.name.endswith('.example'):
                dest_name = file.name[:-8]  # Remove .example suffix
                dest = config_dir / dest_name
                if not dest.exists():
                    shutil.copy(file, dest)
                    files_copied.append(dest_name)

    if files_copied:
        print(f"[OK] Copied example config files to {config_dir}: {', '.join(files_copied)}.")
        print("Edit these files before rerunning.")
        sys.exit(0)

    # Always ensure .env exists
    if not env_path.exists():
        env_path.touch()
        print(f"[OK] Created .env at {env_path}. Edit this file before rerunning.")
        sys.exit(0)

    return config_dir, env_path


def load_config() -> dict:
    """
    Load configuration from ~/.documentor/.env.

    Returns:
        Dictionary with configuration values
    """
    config_dir, env_path = ensure_home_config_and_env()
    load_dotenv(dotenv_path=env_path, override=True)

    return {
        "config_dir": config_dir,
        "env_path": env_path,
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
