"""papertrail - AI-powered PDF document classification and organization."""

from papertrail.config import (
    get_repo_root,
    get_config_paths,
    load_env,
    get_openai_client,
)
from papertrail.hashing import hash_file_fast, hash_file_content
from papertrail.logging_utils import setup_failure_logger, log_failure

__all__ = [
    # Config
    "get_repo_root",
    "get_config_paths",
    "load_env",
    "get_openai_client",
    # Hashing
    "hash_file_fast",
    "hash_file_content",
    # Logging
    "setup_failure_logger",
    "log_failure",
]
