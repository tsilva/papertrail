"""Pipeline task."""

import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from archive_extractor import extract_archives

from papertrail.config import get_current_profile
from papertrail.console import get_console
from papertrail.logging_utils import get_logger, setup_task_logging, suppress_console_logging
from papertrail.mbox import extract_mbox_attachments
from papertrail.tasks.validation import validate_merged_pdf

logger = get_logger('cli')


def pipeline(export_date_arg=None, processed_path_override=None):
    """Run the full document processing pipeline."""
    from papertrail.config import get_passwords, resolve_validations_file

    console = get_console()
    start_time = time.time()

    profile = get_current_profile()
    if not profile:
        console.error("No profile is active.", indent=False)
        sys.exit(1)

    raw_dirs = profile.paths.raw
    PROCESSED_FILES_DIR = profile.paths.processed
    EXPORT_FILES_DIR = profile.paths.export

    missing = []
    if not raw_dirs:
        missing.append("paths.raw")
    if not PROCESSED_FILES_DIR:
        missing.append("paths.processed")
    if not EXPORT_FILES_DIR:
        missing.append("paths.export")
    if missing:
        console.error(f"Missing required profile settings: {', '.join(missing)}", indent=False)
        sys.exit(1)

    log_file_path = setup_task_logging(Path(PROCESSED_FILES_DIR), "pipeline")
    logger.debug("=== PIPELINE STARTED ===")
    logger.debug(f"Log: {log_file_path}")

    # Display pipeline header
    console.pipeline_header(profile.profile.name, str(log_file_path))

    if export_date_arg:
        export_dates = [export_date_arg]
    else:
        today = datetime.now()
        first_of_this_month = today.replace(day=1)
        last_month = first_of_this_month - timedelta(days=1)
        export_dates = [last_month.strftime("%Y-%m"), today.strftime("%Y-%m")]

    for ed in export_dates:
        if not re.match(r"^\d{4}-\d{2}$", ed):
            console.error(f"The export_date must be in YYYY-MM format: {ed}", indent=False)
            sys.exit(1)

    passwords, _ = get_passwords()
    if not passwords:
        logger.debug("No passwords configured. Password-protected archives will be skipped.")

    validations_file_path, temp_validations_file = resolve_validations_file()
    if validations_file_path:
        logger.debug(f"Using validations file: {validations_file_path}")

    processed_files_excel_path = Path(PROCESSED_FILES_DIR) / "processed_files.xlsx"

    # Stage 1: Ingest raw files
    if profile.gmail.enabled:
        with console.step_progress("Download Gmail attachments") as step:
            try:
                from papertrail.gmail import download_gmail_attachments

                raw_path = Path(raw_dirs[0])
                raw_path.mkdir(parents=True, exist_ok=True)

                end_date = datetime.now()
                gmail_start = (end_date.replace(day=1) - timedelta(days=1)).replace(day=1)
                logger.debug(f"Gmail date range: {gmail_start.date()} to {end_date.date()}")

                stats = download_gmail_attachments(
                    output_dir=raw_path,
                    start_date=gmail_start,
                    end_date=end_date,
                    quiet=True,
                )
                if stats['attachments_downloaded'] > 0:
                    step.success(
                        f"{stats['messages_processed']} messages processed, "
                        f"{stats['attachments_downloaded']} new attachments"
                    )
                elif stats['messages_processed'] > 0:
                    step.success(f"{stats['messages_processed']} messages processed, 0 new attachments")
                else:
                    step.warning("No messages found")
            except Exception as e:
                step.warning(f"Gmail download failed, continuing pipeline ({e})")
                logger.warning(f"Gmail download failed (non-fatal): {e}")

    for rd in raw_dirs:
        # Mbox extraction
        with console.step_progress("Google Takeout mbox extraction") as step:
            logger.debug("### Google Takeout mbox extraction...")
            stats = extract_mbox_attachments(rd)
            if stats['mbox_files'] > 0:
                step.success(f"{stats['mbox_files']} mbox file(s), {stats['attachments_extracted']} attachment(s)")
                logger.debug(f"Processed {stats['mbox_files']} mbox file(s), extracted {stats['attachments_extracted']} attachment(s)")
            else:
                step.warning("No mbox files found")
            if stats['errors']:
                step.error(f"{len(stats['errors'])} error(s)")
                logger.error(f"Google Takeout mbox extraction encountered {len(stats['errors'])} error(s)")
                sys.exit(1)
            logger.debug("### Google Takeout mbox extraction... Finished.")

        # Archive extraction
        with console.step_progress("Google Takeout archive extraction") as step:
            logger.debug("### Google Takeout archive extraction...")
            results = extract_archives(rd, passwords=passwords if passwords else None)
            total_extracted = 0
            failures = 0
            for archive_path, count in results.items():
                if count == -1:
                    failures += 1
                    logger.debug(f"Failed to extract: {archive_path}")
                else:
                    total_extracted += count
                    logger.debug(f"Extracted {count} files from {archive_path}")
            if total_extracted > 0:
                step.success(f"Extracted {total_extracted} files from {len(results) - failures} archive(s)")
            elif failures > 0:
                step.warning(f"{failures} archive(s) failed")
            else:
                step.warning("No archives found")
            logger.debug("### Google Takeout archive extraction... Finished.")

    # Stage 2: Extract new documents
    from papertrail.tasks.extraction import task_extract_new
    try:
        task_extract_new(Path(PROCESSED_FILES_DIR), [Path(d) for d in raw_dirs])
    except Exception as e:
        console.error(str(e))
        sys.exit(1)

    # Stage 3: Sync orphans
    from papertrail.tasks.extraction import task_sync
    try:
        task_sync(Path(PROCESSED_FILES_DIR))
    except Exception as e:
        console.error(str(e))
        sys.exit(1)

    # Stage 4: Rename files
    from papertrail.tasks.organization import task_rename_files

    with console.step_progress("Rename files") as step:
        try:
            stats = task_rename_files(Path(PROCESSED_FILES_DIR), quiet=True)
            step.success(f"{stats['validated']} files validated, {stats['renamed']} renamed")
        except Exception as e:
            step.error(str(e))
            sys.exit(1)

    # Stage 5: Export to Excel
    from papertrail.tasks.export import export_metadata_to_excel

    with console.step_progress("Export to Excel") as step:
        try:
            stats = export_metadata_to_excel(
                Path(PROCESSED_FILES_DIR), str(processed_files_excel_path), quiet=True
            )
            if stats['exported']:
                step.success(f"Exported {stats['exported']} entries")
            else:
                step.warning("No valid metadata found to export")
        except Exception as e:
            step.error(str(e))
            sys.exit(1)

    from papertrail.tasks.organization import copy_matching_files
    from papertrail.tasks.validation import check_files_exist

    export_file_config = None
    if profile.export.file_mappings.enabled:
        export_file_config = profile.export.file_mappings

    all_validation_missing_items = []

    for export_date in export_dates:
        export_date_dir = os.path.join(EXPORT_FILES_DIR, export_date)

        # Stage 6: Build monthly export
        if os.path.exists(export_date_dir):
            shutil.rmtree(export_date_dir)

        with console.step_progress(f"Copy matching documents ({export_date})") as step:
            try:
                copy_stats = copy_matching_files(
                    Path(PROCESSED_FILES_DIR),
                    export_date,
                    Path(export_date_dir),
                    export_config=export_file_config,
                )
                copied = copy_stats.get('copied', 0)
                if copied:
                    step.success(f"Copied {copied} files to {Path(export_date_dir).name}")
                else:
                    step.success("Completed")
            except Exception as e:
                step.error(str(e))
                sys.exit(1)

        with console.step_progress(f"Merge PDFs ({export_date})") as step:
            logger.debug("### Merge PDFs...")
            try:
                from pdf_gluer import merge_all_pdfs
                with suppress_console_logging():
                    merge_all_pdfs(export_date_dir)
                step.success("Completed")
                logger.debug("### Merge PDFs... Finished.")
            except Exception as e:
                step.error(f"PDF merge failed: {e}")
                logger.error(f"Merge PDFs failed: {e}")
                sys.exit(1)

        with suppress_console_logging():
            validate_merged_pdf(Path(export_date_dir))

        # Stage 7: Validate exported files
        with console.step_progress(f"Validate exported files ({export_date})") as step:
            if validations_file_path:
                try:
                    stats = check_files_exist(
                        Path(export_date_dir), Path(validations_file_path), quiet=True
                    )
                    if stats['all_passed']:
                        step.success(f"{stats['passed']} checks passed")
                    else:
                        step.warning(f"{stats['passed']} checks passed, {stats['missing']} missing")
                        all_validation_missing_items.extend(stats['missing_items'])
                except Exception as e:
                    step.error(str(e))
                    sys.exit(1)
            else:
                step.warning("Skipped (no validation rules configured)")
                logger.debug("Skipping file validation (no validation rules configured in profile)")

    for item in all_validation_missing_items:
        console.warning(item)

    if temp_validations_file:
        try:
            os.unlink(temp_validations_file)
            logger.debug(f"Cleaned up temporary validations file: {temp_validations_file}")
        except Exception as e:
            logger.debug(f"Failed to cleanup temporary validations file: {e}")

    # Stage 8: Reconcile bank statements (runs last, after all validation)
    from papertrail.tasks.reconciliation import _discover_bank_statements, _reconcile_single
    for export_date in export_dates:
        export_date_dir = os.path.join(EXPORT_FILES_DIR, export_date)
        if not os.path.exists(export_date_dir):
            continue
        bank_statements = _discover_bank_statements(Path(export_date_dir))
        for bs_path in bank_statements:
            with console.step_progress(f"Reconcile: {bs_path.name} ({export_date})") as step:
                try:
                    _reconcile_single(Path(export_date_dir), bs_path, dry_run=False, console=console)
                    step.success("Completed")
                except Exception as e:
                    step.warning(f"Reconciliation failed: {e}")
                    logger.warning(f"Reconciliation failed for {bs_path.name}: {e}")

    # Show pipeline footer with elapsed time
    elapsed = time.time() - start_time
    console.pipeline_footer(elapsed_seconds=elapsed)
    logger.debug("All steps completed successfully.")
