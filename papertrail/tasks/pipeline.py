"""Pipeline task."""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from archive_extractor import extract_archives

from papertrail.config import get_current_profile
from papertrail.console import get_console
from papertrail.logging_utils import get_logger, setup_task_logging
from papertrail.mbox import extract_mbox_attachments
from papertrail.tasks.validation import validate_merged_pdf

logger = get_logger('cli')


@contextmanager
def suppress_console_logging():
    """Temporarily suppress console logging output.

    Raises the level of all StreamHandlers on the root logger to suppress
    console output while allowing file logging to continue.
    """
    root = logging.getLogger()
    original_levels = []

    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            original_levels.append((handler, handler.level))
            handler.setLevel(logging.CRITICAL + 1)

    try:
        yield
    finally:
        for handler, level in original_levels:
            handler.setLevel(level)


def run_step(cmd: str, step_desc: str) -> tuple[str, str]:
    """Run a pipeline step, capturing output to the pipeline log.

    Args:
        cmd: Command to execute.
        step_desc: Human-readable step description.

    Returns:
        Tuple of (stdout, stderr) from the command.
    """
    logger.debug(f"### {step_desc}...")
    result = subprocess.run(cmd, shell=True, text=True, capture_output=True)

    # Log all output to file
    if result.stdout:
        for line in result.stdout.rstrip().split('\n'):
            logger.debug(line)
    if result.stderr:
        for line in result.stderr.rstrip().split('\n'):
            logger.debug(line)

    if result.returncode != 0:
        logger.error(f"{step_desc} failed with exit code {result.returncode}.")
        # Surface last meaningful stderr/stdout line in the error
        detail = ""
        for output in (result.stderr, result.stdout):
            if output:
                lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
                if lines:
                    detail = f": {lines[-1]}"
                    break
        raise RuntimeError(f"Failed with exit code {result.returncode}{detail}")

    logger.debug(f"### {step_desc}... Finished.")
    return result.stdout, result.stderr


def _parse_step_output(stdout: str, stderr: str) -> dict:
    """Parse step output to extract summary statistics.

    Args:
        stdout: Standard output from the step.
        stderr: Standard error from the step.

    Returns:
        Dictionary with extracted statistics.
    """
    combined = stdout + stderr
    stats = {}

    # Try to extract common patterns
    # e.g., "13 PDFs scanned, 0 new to process"
    if match := re.search(r'(\d+)\s+PDFs?\s+scanned', combined):
        stats['scanned'] = int(match.group(1))
    if match := re.search(r'(\d+)\s+new\s+to\s+process', combined):
        stats['new'] = int(match.group(1))
    # e.g., "3194 files validated, 0 renamed"
    if match := re.search(r'(\d+)\s+files?\s+validated', combined):
        stats['validated'] = int(match.group(1))
    if match := re.search(r'(\d+)\s+renamed', combined):
        stats['renamed'] = int(match.group(1))
    # e.g., "Exported 3194 entries"
    if match := re.search(r'Exported\s+(\d+)\s+entr', combined):
        stats['exported'] = int(match.group(1))
    # e.g., "Copied 14 files"
    if match := re.search(r'Copied\s+(\d+)\s+files?', combined):
        stats['copied'] = int(match.group(1))

    return stats


def pipeline(export_date_arg=None, processed_path_override=None):
    """Run the full document processing pipeline."""
    from papertrail.config import get_passwords, get_validations

    console = get_console()
    start_time = time.time()

    # Resolve main.py path for subprocess calls
    main_script = str(Path(__file__).parents[1].parent / "main.py")

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

    # Note: API accessibility is checked in initialize_config() before any task runs

    log_file_path = setup_task_logging(Path(PROCESSED_FILES_DIR), "pipeline")
    logger.debug("=== PIPELINE STARTED ===")
    logger.debug(f"Log: {log_file_path}")

    # Display pipeline header
    console.pipeline_header(profile.profile.name, str(log_file_path))

    if export_date_arg:
        export_date = export_date_arg
    else:
        today = datetime.now()
        first_of_this_month = today.replace(day=1)
        last_month = first_of_this_month - timedelta(days=1)
        export_date = last_month.strftime("%Y-%m")

    if not re.match(r"^\d{4}-\d{2}$", export_date):
        console.error("The export_date must be in YYYY-MM format.", indent=False)
        sys.exit(1)

    export_date_dir = os.path.join(EXPORT_FILES_DIR, export_date)

    passwords, _ = get_passwords()
    if not passwords:
        logger.debug("No passwords configured. Password-protected archives will be skipped.")

    temp_validations_file = None

    validations, validations_file = get_validations()

    if validations and validations.get('rules'):
        if validations_file:
            validations_file_path = validations_file
            logger.debug(f"Using validations file from profile: {validations_file_path}")
        else:
            temp_validations = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
            json.dump(validations['rules'], temp_validations, indent=2)
            temp_validations.close()
            validations_file_path = temp_validations.name
            temp_validations_file = temp_validations.name
            logger.debug(f"Created temporary validations file: {validations_file_path}")
    else:
        validations_file_path = None

    processed_files_excel_path = Path(PROCESSED_FILES_DIR) / "processed_files.xlsx"

    # Step 1: Gmail download (skip if disabled, non-fatal on failure)
    if profile.gmail.enabled:
        with console.step_progress("Download Gmail attachments") as step:
            try:
                stdout, _ = run_step(
                    f'"{sys.executable}" "{main_script}" gmail_download',
                    "Download Gmail attachments"
                )
                # Parse and show result
                if "messages processed" in stdout:
                    step.success(stdout.strip().split('\n')[-1] if stdout.strip() else "Completed")
                else:
                    step.success("Completed")
            except RuntimeError as e:
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

    # Extract new documents (called directly for live progress)
    from papertrail.tasks.extraction import task_extract_new
    try:
        task_extract_new(Path(PROCESSED_FILES_DIR), [Path(d) for d in raw_dirs])
    except Exception as e:
        console.error(str(e))
        sys.exit(1)

    # Regenerate orphaned PDFs (called directly for live progress)
    from papertrail.tasks.extraction import task_sync
    try:
        task_sync(Path(PROCESSED_FILES_DIR))
    except Exception as e:
        console.error(str(e))
        sys.exit(1)

    # Rename files
    with console.step_progress("Rename files") as step:
        try:
            stdout, _ = run_step(
                f'"{sys.executable}" "{main_script}" rename_files "{PROCESSED_FILES_DIR}"',
                "Rename files"
            )
            stats = _parse_step_output(stdout, "")
            if stats.get('validated'):
                step.success(f"{stats['validated']} files validated, {stats.get('renamed', 0)} renamed")
            else:
                step.success("Completed")
        except RuntimeError as e:
            step.error(str(e))
            sys.exit(1)

    # Export to Excel
    with console.step_progress("Export to Excel") as step:
        try:
            stdout, _ = run_step(
                f'"{sys.executable}" "{main_script}" export_excel "{PROCESSED_FILES_DIR}" --excel_output_path "{processed_files_excel_path}"',
                "Export to Excel"
            )
            stats = _parse_step_output(stdout, "")
            if stats.get('exported'):
                step.success(f"Exported {stats['exported']} entries")
            else:
                step.success("Completed")
        except RuntimeError as e:
            step.error(str(e))
            sys.exit(1)

    # Purge export date folder before copying
    if os.path.exists(export_date_dir):
        shutil.rmtree(export_date_dir)

    # Copy matching documents
    with console.step_progress(f"Copy matching documents ({export_date})") as step:
        try:
            stdout, _ = run_step(
                f'"{sys.executable}" "{main_script}" copy_matching "{PROCESSED_FILES_DIR}" --pattern "{export_date}" --copy_dest_folder "{export_date_dir}"',
                f"Copy matching documents ({export_date})"
            )
            stats = _parse_step_output(stdout, "")
            if stats.get('copied'):
                step.success(f"Copied {stats['copied']} files to {Path(export_date_dir).name}")
            else:
                step.success("Completed")
        except RuntimeError as e:
            step.error(str(e))
            sys.exit(1)

    # Merge PDFs using pdf_gluer package
    with console.step_progress("Merge PDFs") as step:
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

    # Apply export file mappings (if configured)
    if profile.export.file_mappings.enabled:
        with console.step_progress("Apply export file mappings") as step:
            logger.debug("### Apply export file mappings...")
            from papertrail.tasks.export_mappings import _process_date_folder
            config = profile.export.file_mappings
            stats = _process_date_folder(Path(export_date_dir), config, dry_run=False)
            if stats['remapped'] > 0 or stats['copied'] > 0:
                step.success(f"{stats['remapped']} remapped, {stats['copied']} copied")
            else:
                step.warning("No files to process")
            logger.debug("### Apply export file mappings... Finished.")

    # Validate exported files
    with console.step_progress("Validate exported files") as step:
        if validations_file_path:
            try:
                stdout, stderr = run_step(
                    f'"{sys.executable}" "{main_script}" check_files_exist "{export_date_dir}" --check_schema_path "{validations_file_path}"',
                    "Validate exported files"
                )
                # Parse validation results - look for pass/fail counts
                combined = stdout + stderr
                if match := re.search(r'(\d+)\s+checks?\s+passed.*?(\d+)\s+missing', combined):
                    passed, missing_count = int(match.group(1)), int(match.group(2))
                    if missing_count > 0:
                        step.warning(f"{passed} checks passed, {missing_count} missing")
                    else:
                        step.success(f"{passed} checks passed")
                else:
                    step.success("Validation completed")
            except RuntimeError as e:
                step.error(str(e))
                sys.exit(1)
        else:
            step.warning("Skipped (no validation rules configured)")
            logger.debug("Skipping file validation (no validation rules configured in profile)")

    if temp_validations_file:
        try:
            os.unlink(temp_validations_file)
            logger.debug(f"Cleaned up temporary validations file: {temp_validations_file}")
        except Exception as e:
            logger.debug(f"Failed to cleanup temporary validations file: {e}")

    # Show pipeline footer with elapsed time
    elapsed = time.time() - start_time
    console.pipeline_footer(elapsed_seconds=elapsed)
    logger.debug("All steps completed successfully.")
