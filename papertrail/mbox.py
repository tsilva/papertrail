"""Mbox extraction wrapper module."""

from pathlib import Path

from mbox_extractor import extract_mbox

from papertrail.logging_utils import get_logger

logger = get_logger('cli')


def find_mbox_files(directory: Path) -> list[Path]:
    """Find all .mbox files recursively in directory."""
    return list(directory.rglob("*.mbox"))


def extract_mbox_attachments(directory: str | Path) -> dict:
    """Extract attachments from all mbox files in a directory.

    Mimics the CLI behavior of mbox-extractor: extracts attachments to the
    same directory as each mbox file.

    Args:
        directory: Directory to search for mbox files.

    Returns:
        Dictionary with extraction stats:
        - mbox_files: Number of mbox files processed
        - attachments_extracted: Total attachments extracted
        - errors: List of error messages (if any)
    """
    directory = Path(directory)
    stats = {
        'mbox_files': 0,
        'attachments_extracted': 0,
        'errors': [],
    }

    mbox_files = find_mbox_files(directory)
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
