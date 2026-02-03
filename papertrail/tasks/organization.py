"""File renaming and organization tasks."""

import re
import shutil
import unicodedata
from pathlib import Path

from papertrail.hashing import hash_file_fast
from papertrail.logging_utils import get_logger
from papertrail.models import DocumentMetadata
from papertrail.tasks import task_log_context

logger = get_logger('cli')


def sanitize_filename_component(s: str) -> str:
    """Sanitize a string for use in a filename."""
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[\\/*?:"<>|()\[\],]', '', s).strip()
    return re.sub(r'\s+', ' ', s)


def file_name_from_metadata(metadata: DocumentMetadata, file_hash: str) -> str:
    """Generate a filename from metadata."""
    parts = [
        sanitize_filename_component(metadata.issue_date),
        sanitize_filename_component(metadata.document_type.value),
        sanitize_filename_component(metadata.issuing_party.value)
    ]

    if metadata.service_name:
        parts.append(sanitize_filename_component(metadata.service_name))

    if metadata.total_amount is not None:
        amount = f"{metadata.total_amount:.0f}" if metadata.total_amount.is_integer() else f"{metadata.total_amount:.2f}"
        currency = metadata.total_amount_currency or ""
        parts.append(sanitize_filename_component(f"{amount} {currency}".strip()))

    parts.append(f"{file_hash}.pdf")
    return " - ".join(parts).lower()


def task_rename_files(processed_path: Path):
    """Rename existing PDF files based on metadata."""
    from papertrail.metadata import load_json_files_parallel

    with task_log_context(processed_path, "rename_files"):
        logger.info("Renaming existing PDF files and metadata based on metadata...")

        valid_entries = []

        for metadata_path, metadata in load_json_files_parallel(processed_path, validate=True, show_progress=True, progress_desc="Validating metadata"):
            pdf_path = metadata_path.with_suffix(".pdf")
            if not pdf_path.exists():
                logger.warning(f"Skipping {metadata_path.name}: PDF file not found")
                continue

            valid_entries.append((pdf_path, metadata))

        logger.info(f"Found {len(valid_entries)} files to rename")

        renamed_count = 0
        for old_pdf_path, metadata in valid_entries:
            content_hash = metadata.content_hash
            new_filename = file_name_from_metadata(metadata, content_hash)
            new_pdf_path = processed_path / new_filename
            new_metadata_path = new_pdf_path.with_suffix(".json")

            if old_pdf_path == new_pdf_path:
                continue

            try:
                old_metadata_path = old_pdf_path.with_suffix(".json")
                shutil.move(old_pdf_path, new_pdf_path)
                shutil.move(old_metadata_path, new_metadata_path)
                renamed_count += 1
                if renamed_count <= 10 or renamed_count % 100 == 0:
                    logger.info(f"[{renamed_count}] Renamed: {old_pdf_path.name} -> {new_filename}")
            except Exception as e:
                logger.error(f"Failed to rename {old_pdf_path.name}: {e}")

        logger.info(f"Renaming complete. Renamed {renamed_count} files.")


def copy_matching_files(
    processed_path: Path,
    regex_pattern: str,
    dest_folder: Path,
    incremental: bool = False
) -> dict:
    """Copy files matching regex pattern to destination."""
    dest_folder.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(regex_pattern)
    stats = {'copied': 0, 'skipped': 0, 'total': 0}

    for file in processed_path.iterdir():
        if not file.is_file():
            continue
        if file.suffix.lower() not in [".pdf", ".json"]:
            continue
        if not pattern.search(file.name):
            continue

        stats['total'] += 1
        dest_file = dest_folder / file.name

        should_copy = True
        if incremental and dest_file.exists():
            if file.stat().st_size == dest_file.stat().st_size:
                src_hash = hash_file_fast(file)
                dst_hash = hash_file_fast(dest_file)
                if src_hash == dst_hash:
                    should_copy = False
                    stats['skipped'] += 1

        if should_copy:
            shutil.copy2(file, dest_file)
            stats['copied'] += 1

    return stats
