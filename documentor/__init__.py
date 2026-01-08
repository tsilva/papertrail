"""Documentor - AI-powered PDF document classification and organization."""

from documentor.config import (
    get_config_dir_and_env_path,
    ensure_home_config_and_env,
    load_config,
    get_openai_client,
)
from documentor.hashing import hash_file_fast, hash_file_content
from documentor.logging_utils import setup_failure_logger, log_failure

__all__ = [
    # Config
    "get_config_dir_and_env_path",
    "ensure_home_config_and_env",
    "load_config",
    "get_openai_client",
    # Hashing
    "hash_file_fast",
    "hash_file_content",
    # Logging
    "setup_failure_logger",
    "log_failure",
]
