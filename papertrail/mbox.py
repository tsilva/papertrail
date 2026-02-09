"""Mbox extraction wrapper module."""

from pathlib import Path

from mbox_extractor import extract_mbox

from papertrail.logging_utils import get_logger

logger = get_logger('cli')


def extract_mbox_attachments(directory: str | Path) -> dict:
    """Extract attachments from all mbox files in a directory."""
    directory = Path(directory)
    stats = {
        'mbox_files': 0,
        'attachments_extracted': 0,
        'errors': [],
    }

    mbox_files = list(directory.rglob("*.mbox"))
    if not mbox_files:
        logger.info(f"No mbox files found in {directory}")
        return stats

    logger.info(f"Found {len(mbox_files)} mbox file(s) in {directory}")

    for mbox_path in mbox_files:
        output_dir = mbox_path.parent
        logger.info(f"Processing: {mbox_path.name}")

        try:
            count = extract_mbox(
                str(mbox_path),
                str(output_dir),
                show_progress=True,
            )
            stats['mbox_files'] += 1
            stats['attachments_extracted'] += count
            logger.info(f"  Extracted {count} attachment(s)")
        except Exception as e:
            error_msg = f"Error processing {mbox_path}: {e}"
            logger.error(error_msg)
            stats['errors'].append(error_msg)

    return stats
