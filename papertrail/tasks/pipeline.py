"""Pipeline task."""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from archive_extractor import extract_archives

from papertrail.config import get_current_profile
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
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False


def run_step(cmd, step_desc):
    """Run a pipeline step, capturing output to the pipeline log."""
    logger.info(f"### {step_desc}...")
    result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    if result.stdout:
        for line in result.stdout.rstrip().split('\n'):
            logger.info(line)
    if result.stderr:
        for line in result.stderr.rstrip().split('\n'):
            logger.info(line)
    if result.returncode != 0:
        logger.error(f"{step_desc} failed with exit code {result.returncode}.")
        sys.exit(result.returncode)
    logger.info(f"### {step_desc}... Finished.")


def pipeline(export_date_arg=None, processed_path_override=None):
    """Run the full document processing pipeline."""
    from shutil import which
    from papertrail.config import get_passwords, get_validations

    # Resolve main.py path for subprocess calls
    main_script = str(Path(__file__).parents[1].parent / "main.py")

    profile = get_current_profile()
    if not profile:
        logger.error("No profile is active.")
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
        logger.error(f"Missing required profile settings: {', '.join(missing)}")
        sys.exit(1)

    # Check API accessibility before starting pipeline
    base_url = profile.openrouter.base_url
    logger.info(f"Checking API accessibility: {base_url}")
    if not check_api_accessibility(base_url):
        logger.error(f"API base URL is not accessible: {base_url}")
        logger.error("Please check your network connection and the base_url in your profile.")
        sys.exit(1)

    log_file_path = setup_task_logging(Path(PROCESSED_FILES_DIR), "pipeline")
    logger.info("=== PIPELINE STARTED ===")
    logger.info(f"Log: {log_file_path}")

    if which("pdf-merger") is None:
        logger.error("Required tool 'pdf-merger' not found in PATH. Please install it and try again.")
        sys.exit(1)

    if export_date_arg:
        export_date = export_date_arg
    else:
        today = datetime.now()
        first_of_this_month = today.replace(day=1)
        last_month = first_of_this_month - timedelta(days=1)
        export_date = last_month.strftime("%Y-%m")

    if not re.match(r"^\d{4}-\d{2}$", export_date):
        logger.error("The export_date must be in YYYY-MM format.")
        sys.exit(1)

    export_date_dir = os.path.join(EXPORT_FILES_DIR, export_date)

    passwords, _ = get_passwords()
    if not passwords:
        logger.warning("No passwords configured. Password-protected archives will be skipped.")

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

    run_step(f'"{sys.executable}" "{main_script}" gmail_download', "Step 1: Download Gmail attachments")

    for rd in raw_dirs:
        logger.info("### Step 2: Google Takeout mbox extraction...")
        stats = extract_mbox_attachments(rd)
        if stats['mbox_files'] > 0:
            logger.info(f"Processed {stats['mbox_files']} mbox file(s), extracted {stats['attachments_extracted']} attachment(s)")
        if stats['errors']:
            logger.error(f"Step 2 encountered {len(stats['errors'])} error(s)")
            sys.exit(1)
        logger.info("### Step 2: Google Takeout mbox extraction... Finished.")

        logger.info("### Step 3: Google Takeout zip extraction...")
        results = extract_archives(rd, passwords=passwords if passwords else None)
        for archive_path, count in results.items():
            if count == -1:
                logger.warning(f"Failed to extract: {archive_path}")
            else:
                logger.info(f"Extracted {count} files from {archive_path}")
        logger.info("### Step 3: Google Takeout zip extraction... Finished.")

    raw_dirs_arg = ";".join(raw_dirs)
    run_step(f'"{sys.executable}" "{main_script}" extract_new "{PROCESSED_FILES_DIR}" --raw_path "{raw_dirs_arg}"', "Step 4: Extract new documents")
    run_step(f'"{sys.executable}" "{main_script}" rename_files "{PROCESSED_FILES_DIR}"', "Step 5: Rename files and metadata")
    run_step(f'"{sys.executable}" "{main_script}" export_excel "{PROCESSED_FILES_DIR}" --excel_output_path "{processed_files_excel_path}"', "Step 6: Export metadata to Excel")
    run_step(f'"{sys.executable}" "{main_script}" copy_matching "{PROCESSED_FILES_DIR}" --regex_pattern "{export_date}" --copy_dest_folder "{export_date_dir}"', "Step 7: Copy matching documents")
    run_step(f'pdf-merger "{export_date_dir}"', "Step 8: Merge PDFs")
    validate_merged_pdf(Path(export_date_dir))
    if validations_file_path:
        run_step(f'"{sys.executable}" "{main_script}" check_files_exist "{export_date_dir}" --check_schema_path "{validations_file_path}"', "Step 9: Validate exported files")
    else:
        logger.info("Step 9: Skipping file validation (no validation rules configured in profile)")

    if temp_validations_file:
        try:
            os.unlink(temp_validations_file)
            logger.debug(f"Cleaned up temporary validations file: {temp_validations_file}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temporary validations file: {e}")

    logger.info("All steps completed successfully.")
