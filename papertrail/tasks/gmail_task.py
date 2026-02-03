"""Gmail download task."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

from papertrail.config import get_current_profile
from papertrail.console import get_console
from papertrail.logging_utils import get_logger, setup_task_logging
from papertrail.metadata import get_unique_dates

logger = get_logger('cli')


def task_gmail_download():
    """Download email attachments from Gmail."""
    from papertrail.gmail import download_gmail_attachments

    console = get_console()

    profile = get_current_profile()
    if not profile:
        console.error("No profile is active.", indent=False)
        sys.exit(1)

    raw_paths = profile.paths.raw
    processed_path_str = profile.paths.processed

    if processed_path_str:
        setup_task_logging(Path(processed_path_str), "gmail_download")

    if not raw_paths or not processed_path_str:
        missing = []
        if not raw_paths:
            missing.append("paths.raw")
        if not processed_path_str:
            missing.append("paths.processed")
        console.error(f"Missing required profile settings: {', '.join(missing)}", indent=False)
        sys.exit(1)

    raw_path = Path(raw_paths[0])
    processed_path = Path(processed_path_str)

    raw_path.mkdir(parents=True, exist_ok=True)

    end_date = datetime.now()

    unique_dates = get_unique_dates(processed_path) if processed_path.exists() else []

    if unique_dates:
        most_recent = unique_dates[0]
        start_date = datetime.strptime(f"{most_recent}-01", "%Y-%m-%d")
        logger.debug(f"Date range: {start_date.date()} to {end_date.date()}")
    else:
        start_date = end_date - timedelta(days=30)
        logger.debug("No processed files found. Using default range: last 30 days")

    logger.debug(f"Downloading attachments to: {raw_path}")

    stats = download_gmail_attachments(
        output_dir=raw_path,
        start_date=start_date,
        end_date=end_date,
    )

    # Log details to file
    logger.debug(f"Messages found: {stats['messages_found']}")
    logger.debug(f"Messages processed: {stats['messages_processed']}")
    logger.debug(f"Messages skipped: {stats['messages_skipped']}")
    logger.debug(f"Attachments downloaded: {stats['attachments_downloaded']}")
    logger.debug(f"Attachments failed: {stats['attachments_failed']}")
    logger.debug(f"Bytes downloaded: {stats['bytes_downloaded']:,}")

    # Console output - compact summary
    if stats['attachments_downloaded'] > 0:
        console.success(
            f"{stats['messages_processed']} messages processed, "
            f"{stats['attachments_downloaded']} new attachments",
            indent=False
        )
    elif stats['messages_processed'] > 0:
        console.success(
            f"{stats['messages_processed']} messages processed, 0 new attachments",
            indent=False
        )
    else:
        console.warning("No messages found", indent=False)
