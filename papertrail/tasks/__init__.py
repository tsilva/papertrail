"""
papertrail.tasks - Task modules for the papertrail CLI.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from papertrail.logging_utils import setup_task_logging, get_logger
from papertrail.console import get_console

logger = get_logger('cli')


@contextmanager
def task_log_context(
    processed_path: Path,
    task_name: str,
    show_header: bool = True,
) -> Generator[Path, None, None]:
    """Context manager for task logging boilerplate.

    Args:
        processed_path: Path to the processed documents directory.
        task_name: Name of the task (used in log filename).
        show_header: If True, display task header in console.

    Yields:
        Path to the created log file.
    """
    log_file_path = setup_task_logging(processed_path, task_name)
    console = get_console()

    # Log to file (always)
    logger.debug(f"=== {task_name.upper()} STARTED ===")
    logger.debug(f"Log: {log_file_path}")

    # Console output (only if show_header is True)
    if show_header:
        console.detail(f"Log: {log_file_path}", indent=False)

    yield log_file_path


def require_initialized(manager, name: str):
    """Raise if a manager is None (not initialized)."""
    if manager is None:
        logger.error(f"{name} not initialized.")
        raise RuntimeError(f"{name} not initialized.")


# Re-export all task functions
from papertrail.tasks.extraction import (
    classify_pdf_document,
    task_extract_new,
    task_reextract,
    task_validate_extraction,
    task_regenerate_orphans,
)
from papertrail.tasks.organization import (
    task_rename_files,
    copy_matching_files,
)
from papertrail.tasks.validation import (
    validate_metadata,
    validate_merged_pdf,
    check_files_exist,
    task_backfill_page_count,
)
from papertrail.tasks.export import (
    export_metadata_to_excel,
    task_export_all_dates,
)
from papertrail.tasks.mappings_tasks import (
    task_bootstrap_mappings,
    task_review_mappings,
    task_add_canonical,
    task_review_rejected,
)
from papertrail.tasks.gmail_task import task_gmail_download
from papertrail.tasks.pipeline import pipeline
from papertrail.tasks.qr_inventory import task_qr_inventory
