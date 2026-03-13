"""Compatibility wrappers for organize-related workflows."""

from pathlib import Path

from papertrail.logging_utils import get_logger
from papertrail.rules import RuleEngine
from papertrail.workflows import (
    copy_matching_files,
    export_metadata_to_excel,
    task_archive,
    task_export_all_dates,
    task_gmail_download,
    task_rename_files,
)

logger = get_logger("cli")


def merge_reconciled_attachments(
    export_path: Path,
    all_matches: list,
    merge_rules: list,
) -> dict:
    """Merge attachment PDFs into target PDFs based on reconciliation match data."""
    stats = {"merged": 0, "skipped": 0, "errors": 0}

    if not merge_rules or not all_matches:
        return stats

    merged_attachments: set[str] = set()
    engine = RuleEngine()

    for match in all_matches:
        for target, attachment in engine.select_merge_pairs(match, merge_rules):
            target_pdf = export_path / target.pdf_filename
            if not target_pdf.exists() or target_pdf.suffix.lower() != ".pdf":
                continue

            if attachment.pdf_filename in merged_attachments:
                logger.debug(
                    f"[MERGE] Skipping {attachment.pdf_filename} - already merged elsewhere"
                )
                stats["skipped"] += 1
                continue

            attach_pdf = export_path / attachment.pdf_filename
            if not attach_pdf.exists() or attach_pdf.suffix.lower() != ".pdf":
                continue

            if target_pdf == attach_pdf:
                continue

            try:
                import pikepdf

                with pikepdf.open(target_pdf, allow_overwriting_input=True) as target_doc:
                    with pikepdf.open(attach_pdf) as attach_doc:
                        target_doc.pages.extend(attach_doc.pages)
                    target_doc.save(target_pdf)

                merged_attachments.add(attachment.pdf_filename)
                stats["merged"] += 1
                logger.debug(
                    f"[MERGE] Appended {attachment.pdf_filename} "
                    f"to {target.pdf_filename}"
                )
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    f"[MERGE] Failed to append {attachment.pdf_filename} "
                    f"to {target.pdf_filename}: {exc}"
                )

    return stats


__all__ = [
    "copy_matching_files",
    "export_metadata_to_excel",
    "merge_reconciled_attachments",
    "task_archive",
    "task_export_all_dates",
    "task_gmail_download",
    "task_rename_files",
]
