"""Gmail download task."""

from datetime import datetime, timedelta
from pathlib import Path

from papertrail.config import get_current_profile
from papertrail.console import get_console
from papertrail.logging_utils import get_logger, setup_task_logging

logger = get_logger('cli')


def task_gmail_download():
    """Download email attachments from Gmail."""
    from papertrail.gmail import download_gmail_attachments

    console = get_console()

    profile = get_current_profile()
    if not profile:
        console.error("No profile is active.", indent=False)
        raise RuntimeError("No profile is active.")

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
        raise RuntimeError(f"Missing required profile settings: {', '.join(missing)}")

    raw_path = Path(raw_paths[0])
    processed_path = Path(processed_path_str)

    raw_path.mkdir(parents=True, exist_ok=True)

    end_date = datetime.now()

    # Default: current month + previous month
    start_date = (end_date.replace(day=1) - timedelta(days=1)).replace(day=1)
    logger.debug(f"Date range: {start_date.date()} to {end_date.date()}")

    logger.debug(f"Downloading attachments to: {raw_path}")

    try:
        stats = download_gmail_attachments(
            output_dir=raw_path,
            start_date=start_date,
            end_date=end_date,
        )
    except FileNotFoundError as e:
        console.error(f"Gmail credentials not found: {e}", indent=False)
        raise RuntimeError(f"Gmail credentials not found: {e}") from e
    except Exception as e:
        error_type = type(e).__name__
        console.error(f"Gmail download failed ({error_type}): {e}", indent=False)
        raise RuntimeError(f"Gmail download failed ({error_type}): {e}") from e

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
