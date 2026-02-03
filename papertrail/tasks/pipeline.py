"""Pipeline task."""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from archive_extractor import extract_archives

from papertrail.config import get_current_profile
from papertrail.console import get_console
from papertrail.logging_utils import get_logger, setup_task_logging
from papertrail.mbox import extract_mbox_attachments
from papertrail.tasks.validation import validate_merged_pdf

logger = get_logger('cli')


def check_api_accessibility(base_url: str, timeout: int = 10) -> bool:
    """Check if the API base URL is accessible."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(base_url, method='HEAD')
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        # HTTP error responses (4xx, 5xx) still mean server is accessible
        return True
    except (urllib.error.URLError, TimeoutError):
        return False


def run_step(cmd: str, step_desc: str, step_num: int) -> tuple[str, str]:
    """Run a pipeline step, capturing output to the pipeline log.

    Args:
        cmd: Command to execute.
        step_desc: Human-readable step description.
        step_num: Step number for display.

    Returns:
        Tuple of (stdout, stderr) from the command.
    """
    console = get_console()
    console.step(step_desc, number=step_num)

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
        console.error(f"Failed with exit code {result.returncode}")
        logger.error(f"{step_desc} failed with exit code {result.returncode}.")
        sys.exit(result.returncode)

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

    # Check API accessibility before starting pipeline
    base_url = profile.openrouter.base_url
    logger.debug(f"Checking API accessibility: {base_url}")
    if not check_api_accessibility(base_url):
        console.error(f"API base URL is not accessible: {base_url}", indent=False)
        console.error("Please check your network connection and the base_url in your profile.", indent=False)
        sys.exit(1)

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

    # Step 1: Gmail download
    stdout, _ = run_step(
        f'"{sys.executable}" "{main_script}" gmail_download',
        "Download Gmail attachments",
        step_num=1
    )
    # Parse and show result
    if "messages processed" in stdout:
        console.success(stdout.strip().split('\n')[-1] if stdout.strip() else "Completed")
    else:
        console.success("Completed")

    step_num = 2
    for rd in raw_dirs:
        # Step 2: Mbox extraction
        console.step("Google Takeout mbox extraction", number=step_num)
        logger.debug(f"### Step {step_num}: Google Takeout mbox extraction...")
        stats = extract_mbox_attachments(rd)
        if stats['mbox_files'] > 0:
            console.success(f"{stats['mbox_files']} mbox file(s), {stats['attachments_extracted']} attachment(s)")
            logger.debug(f"Processed {stats['mbox_files']} mbox file(s), extracted {stats['attachments_extracted']} attachment(s)")
        else:
            console.warning("No mbox files found")
        if stats['errors']:
            console.error(f"{len(stats['errors'])} error(s)")
            logger.error(f"Step {step_num} encountered {len(stats['errors'])} error(s)")
            sys.exit(1)
        logger.debug(f"### Step {step_num}: Google Takeout mbox extraction... Finished.")
        step_num += 1

        # Step 3: Archive extraction
        console.step("Google Takeout archive extraction", number=step_num)
        logger.debug(f"### Step {step_num}: Google Takeout archive extraction...")
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
            console.success(f"Extracted {total_extracted} files from {len(results) - failures} archive(s)")
        elif failures > 0:
            console.warning(f"{failures} archive(s) failed")
        else:
            console.warning("No archives found")
        logger.debug(f"### Step {step_num}: Google Takeout archive extraction... Finished.")
        step_num += 1

    # Step 4: Extract new documents
    stdout, _ = run_step(
        f'"{sys.executable}" "{main_script}" extract_new "{PROCESSED_FILES_DIR}" --raw_path "{";".join(raw_dirs)}"',
        "Extract new documents",
        step_num=step_num
    )
    stats = _parse_step_output(stdout, "")
    if stats.get('scanned'):
        console.success(f"{stats['scanned']} PDFs scanned, {stats.get('new', 0)} new to process")
    else:
        console.success("Completed")
    step_num += 1

    # Step 5: Rename files
    stdout, _ = run_step(
        f'"{sys.executable}" "{main_script}" rename_files "{PROCESSED_FILES_DIR}"',
        "Rename files",
        step_num=step_num
    )
    stats = _parse_step_output(stdout, "")
    if stats.get('validated'):
        console.success(f"{stats['validated']} files validated, {stats.get('renamed', 0)} renamed")
    else:
        console.success("Completed")
    step_num += 1

    # Step 6: Export to Excel
    stdout, _ = run_step(
        f'"{sys.executable}" "{main_script}" export_excel "{PROCESSED_FILES_DIR}" --excel_output_path "{processed_files_excel_path}"',
        "Export to Excel",
        step_num=step_num
    )
    stats = _parse_step_output(stdout, "")
    if stats.get('exported'):
        console.success(f"Exported {stats['exported']} entries")
    else:
        console.success("Completed")
    step_num += 1

    # Step 7: Copy matching documents
    stdout, _ = run_step(
        f'"{sys.executable}" "{main_script}" copy_matching "{PROCESSED_FILES_DIR}" --regex_pattern "{export_date}" --copy_dest_folder "{export_date_dir}"',
        f"Copy matching documents ({export_date})",
        step_num=step_num
    )
    stats = _parse_step_output(stdout, "")
    if stats.get('copied'):
        console.success(f"Copied {stats['copied']} files to {Path(export_date_dir).name}")
    else:
        console.success("Completed")
    step_num += 1

    # Step 8: Merge PDFs using pdf_gluer package
    console.step("Merge PDFs", number=step_num)
    logger.debug(f"### Step {step_num}: Merge PDFs...")
    try:
        from pdf_gluer import merge_all_pdfs
        merge_all_pdfs(export_date_dir)
        console.success("Completed")
        logger.debug(f"### Step {step_num}: Merge PDFs... Finished.")
    except Exception as e:
        console.error(f"PDF merge failed: {e}")
        logger.error(f"Step {step_num}: Merge PDFs failed: {e}")
        sys.exit(1)

    validate_merged_pdf(Path(export_date_dir))
    step_num += 1

    # Step 9: Validate exported files
    if validations_file_path:
        stdout, stderr = run_step(
            f'"{sys.executable}" "{main_script}" check_files_exist "{export_date_dir}" --check_schema_path "{validations_file_path}"',
            "Validate exported files",
            step_num=step_num
        )
        # Parse validation results - look for pass/fail counts
        combined = stdout + stderr
        if match := re.search(r'(\d+)\s+checks?\s+passed.*?(\d+)\s+missing', combined):
            passed, missing_count = int(match.group(1)), int(match.group(2))
            if missing_count > 0:
                console.warning(f"{passed} checks passed, {missing_count} missing")
            else:
                console.success(f"{passed} checks passed")
        else:
            console.success("Validation completed")
    else:
        console.step("Validate exported files", number=step_num)
        console.warning("Skipped (no validation rules configured)")
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
