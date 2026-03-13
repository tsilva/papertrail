"""papertrail.tasks - Task modules for the papertrail CLI."""

from importlib import import_module
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


_EXPORTS = {
    "classify_pdf_document": ("papertrail.tasks.extraction", "classify_pdf_document"),
    "task_extract_new": ("papertrail.tasks.extraction", "task_extract_new"),
    "task_sync": ("papertrail.tasks.extraction", "task_sync"),
    "copy_matching_files": ("papertrail.tasks.organize", "copy_matching_files"),
    "export_metadata_to_excel": ("papertrail.tasks.organize", "export_metadata_to_excel"),
    "merge_reconciled_attachments": ("papertrail.tasks.organize", "merge_reconciled_attachments"),
    "task_archive": ("papertrail.tasks.organize", "task_archive"),
    "task_export_all_dates": ("papertrail.tasks.organize", "task_export_all_dates"),
    "task_gmail_download": ("papertrail.tasks.organize", "task_gmail_download"),
    "task_rename_files": ("papertrail.tasks.organize", "task_rename_files"),
    "validate_merged_pdf": ("papertrail.tasks.check", "validate_merged_pdf"),
    "task_check": ("papertrail.tasks.check", "task_check"),
    "pipeline": ("papertrail.tasks.pipeline", "pipeline"),
    "task_reconcile": ("papertrail.tasks.reconciliation", "task_reconcile"),
}


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = ["task_log_context", *_EXPORTS.keys()]
