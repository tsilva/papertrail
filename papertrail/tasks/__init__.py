"""
papertrail.tasks - Task modules for the papertrail CLI.
"""

from contextlib import contextmanager
from pathlib import Path

from papertrail.logging_utils import setup_task_logging, get_logger

logger = get_logger('cli')


@contextmanager
def task_log_context(processed_path: Path, task_name: str):
    """Context manager for task logging boilerplate."""
    log_file_path = setup_task_logging(processed_path, task_name)
    logger.info(f"=== {task_name.upper()} STARTED ===")
    logger.info(f"Log: {log_file_path}")
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
)
from papertrail.tasks.organization import (
    task_rename_files,
    rename_single_pdf,
    rename_pdf_files,
    file_name_from_metadata,
    sanitize_filename_component,
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
from papertrail.tasks.pipeline import pipeline, run_step
