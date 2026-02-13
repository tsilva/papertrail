"""Gmail download task."""

from pathlib import Path

from papertrail.config import get_current_profile
from papertrail.console import get_console
from papertrail.dates import compute_month_range, month_to_date_range
from papertrail.logging_utils import get_logger, setup_task_logging

logger = get_logger('cli')


def task_gmail_download(months: int = 2):
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

    export_dates = compute_month_range(months)

    totals = {
        "messages_found": 0, "messages_processed": 0, "messages_skipped": 0,
        "attachments_downloaded": 0, "attachments_failed": 0, "bytes_downloaded": 0,
    }

    gmail_dir = raw_path / "gmail"

    for month in export_dates:
        month_dir = gmail_dir / month
        month_dir.mkdir(parents=True, exist_ok=True)

        start_date, end_date = month_to_date_range([month])
        logger.debug(f"Gmail {month}: {start_date.date()} to {end_date.date()} → {month_dir}")

        try:
            stats = download_gmail_attachments(
                output_dir=month_dir,
                start_date=start_date,
                end_date=end_date,
                tracking_dir=gmail_dir,
            )
        except FileNotFoundError as e:
            console.error(f"Gmail credentials not found: {e}", indent=False)
            raise RuntimeError(f"Gmail credentials not found: {e}") from e
        except Exception as e:
            error_type = type(e).__name__
            console.error(f"Gmail download failed ({error_type}): {e}", indent=False)
            raise RuntimeError(f"Gmail download failed ({error_type}): {e}") from e

        for key in totals:
            totals[key] += stats[key]

    logger.debug(f"Messages found: {totals['messages_found']}")
    logger.debug(f"Messages processed: {totals['messages_processed']}")
    logger.debug(f"Messages skipped: {totals['messages_skipped']}")
    logger.debug(f"Attachments downloaded: {totals['attachments_downloaded']}")
    logger.debug(f"Attachments failed: {totals['attachments_failed']}")
    logger.debug(f"Bytes downloaded: {totals['bytes_downloaded']:,}")

    if totals['attachments_downloaded'] > 0:
        console.success(
            f"{totals['messages_processed']} messages processed, "
            f"{totals['attachments_downloaded']} new attachments",
            indent=False
        )
    elif totals['messages_processed'] > 0:
        console.success(
            f"{totals['messages_processed']} messages processed, 0 new attachments",
            indent=False
        )
    else:
        console.warning("No messages found", indent=False)
