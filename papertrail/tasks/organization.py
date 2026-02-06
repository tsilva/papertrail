"""File renaming and organization tasks."""

import re
import shutil
import unicodedata
from pathlib import Path

from papertrail.console import get_console
from papertrail.hashing import hash_file_fast
from papertrail.logging_utils import get_logger, log_failure, DocumentLogger
from papertrail.metadata import save_metadata_json
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
        sanitize_filename_component(metadata.date_issued),
        sanitize_filename_component(metadata.document_type.value),
        sanitize_filename_component(metadata.issuing_party.value)
    ]

    if metadata.document_title:
        parts.append(sanitize_filename_component(metadata.document_title))

    if metadata.total_amount is not None:
        amount = f"{metadata.total_amount:.0f}" if metadata.total_amount.is_integer() else f"{metadata.total_amount:.2f}"
        currency = metadata.total_amount_currency or ""
        parts.append(sanitize_filename_component(f"{amount} {currency}".strip()))

    parts.append(f"{file_hash}.pdf")
    return " - ".join(parts).lower()


def rename_single_pdf(pdf_path: Path, content_hash: str, processed_path: Path,
                      known_content_hashes: set, known_file_hashes: set, failure_logger=None,
                      doc_logger: DocumentLogger = None):
    """Process and rename a single PDF file."""
    from papertrail.tasks.extraction import classify_pdf_document

    try:
        file_hash = hash_file_fast(pdf_path)
        metadata = classify_pdf_document(pdf_path, content_hash, failure_logger, doc_logger=doc_logger)
        metadata.hash_file = file_hash

        filename = file_name_from_metadata(metadata, content_hash)
        new_pdf_path = processed_path / filename

        if new_pdf_path.exists():
            logger.warning(f"Skipping {pdf_path.name}: destination already exists: {filename}")
            return

        shutil.copy2(pdf_path, new_pdf_path)
        save_metadata_json(new_pdf_path, metadata)

        known_content_hashes.add(content_hash)
        known_file_hashes.add(file_hash)
        logger.debug(f"Processed: {pdf_path.name} -> {filename}")
    except Exception as e:
        log_failure(failure_logger, pdf_path, e)
        logger.error(f"Failed to process {pdf_path.name}: {e}")


def rename_pdf_files(pdf_paths, file_hash_map, known_content_hashes, known_file_hashes, processed_path,
                     failure_logger=None, doc_logger: DocumentLogger = None):
    """Rename multiple PDF files."""
    console = get_console()

    with console.progress("Extracting", total=len(pdf_paths)) as progress:
        task = progress.add_task("Extracting", total=len(pdf_paths))
        for pdf_path in pdf_paths:
            name = pdf_path.stem
            if len(name) > 40:
                name = name[:37] + "..."
            progress.update(task, description=f"[dim]{name}[/dim]")
            rename_single_pdf(pdf_path, file_hash_map[pdf_path], processed_path, known_content_hashes, known_file_hashes,
                              failure_logger, doc_logger=doc_logger)
            progress.update(task, advance=1)


def task_rename_files(processed_path: Path):
    """Rename existing PDF files based on metadata."""
    from papertrail.metadata import load_json_files_parallel

    console = get_console()

    with task_log_context(processed_path, "rename_files"):
        logger.debug("Renaming existing PDF files and metadata based on metadata...")

        valid_entries = []

        for metadata_path, metadata in load_json_files_parallel(processed_path, validate=True, show_progress=True, progress_desc="Validating metadata"):
            pdf_path = metadata_path.with_suffix(".pdf")
            if not pdf_path.exists():
                logger.warning(f"Skipping {metadata_path.name}: PDF file not found")
                continue

            valid_entries.append((pdf_path, metadata))

        logger.debug(f"Found {len(valid_entries)} files to validate")

        renamed_count = 0
        for old_pdf_path, metadata in valid_entries:
            content_hash = metadata.hash_content
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
                logger.debug(f"Renamed: {old_pdf_path.name} -> {new_filename}")
            except Exception as e:
                logger.error(f"Failed to rename {old_pdf_path.name}: {e}")

        # Console output
        console.success(f"{len(valid_entries)} files validated, {renamed_count} renamed", indent=False)
        logger.debug(f"Renaming complete. Renamed {renamed_count} files.")


def copy_matching_files(
    processed_path: Path,
    pattern: str,
    dest_folder: Path,
    incremental: bool = False
) -> dict:
    """Copy files matching pattern to destination.

    Args:
        processed_path: Source directory containing files.
        pattern: Unified pattern (glob or regex, auto-detected).
                 Uses partial match (search) by default.
        dest_folder: Destination directory.
        incremental: If True, skip files that already exist with same hash.

    Returns:
        Dict with 'copied', 'skipped', 'total' counts.
    """
    from papertrail.pattern_utils import make_matcher

    console = get_console()
    dest_folder.mkdir(parents=True, exist_ok=True)
    # Use search mode for partial matching
    matcher = make_matcher(pattern, use_search=True)
    stats = {'copied': 0, 'skipped': 0, 'total': 0}

    # First pass: count matching files
    matching_files = []
    for file in processed_path.iterdir():
        if not file.is_file():
            continue
        if file.suffix.lower() not in [".pdf", ".json"]:
            continue
        if not matcher(file.name):
            continue
        matching_files.append(file)

    # Second pass: copy with progress
    with console.progress("Copying files", total=len(matching_files)) as progress:
        task = progress.add_task("Copying files", total=len(matching_files))
        for file in matching_files:
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

            progress.update(task, advance=1)

    # Console output
    # Count only PDF files for the summary (JSON files are copied alongside)
    pdf_copied = stats['copied'] // 2 if stats['copied'] > 0 else 0
    console.success(f"Copied {pdf_copied} files to {dest_folder.name}", indent=False)

    return stats
