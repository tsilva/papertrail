"""Shared workflow helpers."""

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from papertrail.console import get_console
from papertrail.logging_utils import get_logger, setup_task_logging

logger = get_logger("cli")


@contextmanager
def task_log_context(
    processed_path: Path,
    task_name: str,
    show_header: bool = True,
) -> Generator[Path, None, None]:
    """Context manager for task logging setup. Yields the log file path."""
    log_file_path = setup_task_logging(processed_path, task_name)
    console = get_console()

    logger.debug(f"=== {task_name.upper()} STARTED ===")
    logger.debug(f"Log: {log_file_path}")

    if show_header:
        console.detail(f"Log: {log_file_path}", indent=False)

    yield log_file_path
