"""papertrail.tasks - Task modules for the papertrail CLI."""

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
    """Context manager for task logging setup. Yields the log file path."""
    log_file_path = setup_task_logging(processed_path, task_name)
    console = get_console()

    logger.debug(f"=== {task_name.upper()} STARTED ===")
    logger.debug(f"Log: {log_file_path}")

    if show_header:
        console.detail(f"Log: {log_file_path}", indent=False)

    yield log_file_path


from papertrail.tasks.extraction import (
    classify_pdf_document,
    task_extract_new,
    task_sync,
)
from papertrail.tasks.organize import (
    copy_matching_files,
    export_metadata_to_excel,
    merge_reconciled_attachments,
    task_archive,
    task_export_all_dates,
    task_gmail_download,
    task_rename_files,
)
from papertrail.tasks.check import (
    validate_merged_pdf,
    task_check,
)
from papertrail.tasks.pipeline import pipeline
from papertrail.tasks.reconciliation import task_reconcile
