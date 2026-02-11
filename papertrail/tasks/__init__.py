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
    task_validate_extraction,
)
from papertrail.tasks.organization import (
    task_rename_files,
    copy_matching_files,
)
from papertrail.tasks.validation import (
    validate_metadata,
    validate_merged_pdf,
    task_backfill_page_count,
    task_fix_unicode,
)
from papertrail.tasks.export import (
    export_metadata_to_excel,
    task_export_all_dates,
)
from papertrail.tasks.gmail_task import task_gmail_download
from papertrail.tasks.pipeline import pipeline
from papertrail.tasks.qr_inventory import task_qr_inventory
from papertrail.tasks.reconciliation import task_reconcile
