"""Compatibility shim for legacy task imports."""

from papertrail.tasks.extraction import classify_pdf_document
from papertrail.tasks.organize import merge_reconciled_attachments
from papertrail.workflow_utils import task_log_context
from papertrail.workflows import (
    copy_matching_files,
    export_metadata_to_excel,
    pipeline,
    task_archive,
    task_check,
    task_export_all_dates,
    task_extract_new,
    task_gmail_download,
    task_reconcile,
    task_rename_files,
    task_sync,
    validate_merged_pdf,
)

__all__ = [
    "classify_pdf_document",
    "copy_matching_files",
    "export_metadata_to_excel",
    "merge_reconciled_attachments",
    "pipeline",
    "task_archive",
    "task_check",
    "task_export_all_dates",
    "task_extract_new",
    "task_gmail_download",
    "task_log_context",
    "task_reconcile",
    "task_rename_files",
    "task_sync",
    "validate_merged_pdf",
]
