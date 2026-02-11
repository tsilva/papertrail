"""File renaming and organization tasks."""

import re
import shutil
import unicodedata
from pathlib import Path
from typing import Optional

from papertrail.console import get_console
from papertrail.hashing import hash_file_fast
from papertrail.logging_utils import get_logger, log_failure, DocumentLogger
from papertrail.metadata import save_metadata_json, save_json_data, load_json_data, find_companion_file
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
        sanitize_filename_component(metadata.issuing_party)
    ]

    if metadata.document_title:
        title = sanitize_filename_component(metadata.document_title)
        if len(title) > 80:
            title = title[:80].rsplit(" ", 1)[0]
        parts.append(title)

    if metadata.total_amount is not None:
        amount = f"{metadata.total_amount:.0f}" if metadata.total_amount.is_integer() else f"{metadata.total_amount:.2f}"
        currency = metadata.total_amount_currency or ""
        parts.append(sanitize_filename_component(f"{amount} {currency}".strip()))

    ext = getattr(metadata, 'source_extension', None) or '.pdf'
    parts.append(f"{file_hash}{ext}")
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


def task_rename_files(processed_path: Path, quiet: bool = False) -> dict:
    """Rename existing PDF files based on metadata."""
    from papertrail.metadata import load_json_files_parallel

    console = get_console()

    with task_log_context(processed_path, "rename_files", show_header=not quiet):
        logger.debug("Renaming existing PDF files and metadata based on metadata...")

        valid_entries = []

        for metadata_path, metadata in load_json_files_parallel(processed_path, validate=True, show_progress=not quiet, progress_desc="Validating metadata"):
            doc_path = find_companion_file(metadata_path, metadata.model_dump())
            if doc_path is None:
                logger.warning(f"Skipping {metadata_path.name}: companion file not found")
                continue

            valid_entries.append((doc_path, metadata))

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

        if not quiet:
            console.success(f"{len(valid_entries)} files validated, {renamed_count} renamed", indent=False)
        logger.debug(f"Renaming complete. Renamed {renamed_count} files.")

    return {'validated': len(valid_entries), 'renamed': renamed_count}


def _get_nested_value(metadata: dict, key: str):
    """Get a value from metadata using dot notation (e.g., 'qrcode.qr_type')."""
    parts = key.split(".")
    current = metadata
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _match_value(actual, pattern: str) -> bool:
    """Match a metadata value against a pattern (supports trailing wildcard)."""
    if actual is None:
        return False
    actual_str = str(actual)
    if pattern.endswith("*"):
        return actual_str.startswith(pattern[:-1])
    # Numeric comparison for numeric values (handles float repr: 0.0 vs "0")
    if isinstance(actual, (int, float)):
        try:
            return float(actual) == float(pattern)
        except (ValueError, TypeError):
            pass
    return actual_str == pattern


def _resolve_match_value(pattern: str, profile_context: Optional[dict]) -> str:
    """Resolve ${profile.*} variables in a match pattern."""
    if not profile_context or not pattern.startswith("${profile."):
        return pattern
    key = pattern[len("${profile."):-1]
    value = profile_context.get(key)
    return value if value is not None else pattern


def _evaluate_export_prefix(metadata: dict, config, profile_context: Optional[dict] = None) -> str:
    """Evaluate export prefix rules against metadata. First match wins."""
    for rule in config.rules:
        if all(
            _match_value(_get_nested_value(metadata, k), _resolve_match_value(v, profile_context))
            for k, v in rule.match.items()
        ):
            return rule.prefix
    return config.default_prefix


def _build_filename_from_fields(metadata: dict, fields: list, file_hash: str) -> str:
    """Build a filename from selected metadata fields."""
    parts = []
    for field_name in fields:
        value = _get_nested_value(metadata, field_name)
        if value is not None and str(value).strip():
            component = sanitize_filename_component(str(value))
            if len(component) > 80:
                component = component[:80].rsplit(" ", 1)[0]
            parts.append(component)
    ext = metadata.get("source_extension") or ".pdf"
    parts.append(f"{file_hash}{ext}")
    return " - ".join(parts).lower()


def _should_skip_copy(src: Path, dst: Path) -> bool:
    """Check if a copy can be skipped (destination exists with same content)."""
    return (dst.exists()
            and src.stat().st_size == dst.stat().st_size
            and hash_file_fast(src) == hash_file_fast(dst))


def _check_file_size(src: Path, max_file_size_mb: float | None) -> None:
    """Log a warning if file exceeds the configured max size threshold."""
    if max_file_size_mb is None:
        return
    size = src.stat().st_size
    threshold = max_file_size_mb * 1024 * 1024
    if size >= threshold:
        size_mb = size / (1024 * 1024)
        logger.warning(f"Large file: {src.name} ({size_mb:.1f} MB exceeds {max_file_size_mb} MB threshold)")


def copy_matching_files(
    processed_path: Path,
    pattern: str,
    dest_folder: Path,
    incremental: bool = False,
    export_config=None,
    profile_context: Optional[dict] = None,
    quiet: bool = False,
) -> dict:
    """Copy files matching pattern to destination."""
    from papertrail.pattern_utils import make_matcher

    console = get_console()
    dest_folder.mkdir(parents=True, exist_ok=True)
    # Use search mode for partial matching
    matcher = make_matcher(pattern, use_search=True)
    stats = {'copied': 0, 'skipped': 0, 'total': 0}

    file_mappings = export_config.file_mappings if export_config is not None else None
    max_file_size_mb = export_config.max_file_size_mb if export_config is not None else None
    use_prefixes = file_mappings is not None and file_mappings.enabled

    if use_prefixes:
        matching_pdfs = []
        for file in processed_path.iterdir():
            if not file.is_file():
                continue
            if file.suffix.lower() not in (".pdf", ".xlsx"):
                continue
            if not matcher(file.name):
                continue
            matching_pdfs.append(file)

        for pdf_file in console.track(matching_pdfs, "Copying files"):
            stats['total'] += 1
            json_file = pdf_file.with_suffix(".json")

            metadata = {}
            if json_file.exists():
                metadata = load_json_data(json_file)

            prefix = _evaluate_export_prefix(metadata, file_mappings, profile_context)

            if file_mappings.filename_fields and metadata:
                file_hash = metadata.get("hash_content", pdf_file.stem.split(" - ")[-1])
                base_name = _build_filename_from_fields(
                    metadata, file_mappings.filename_fields, file_hash
                )
            else:
                base_name = pdf_file.name

            dest_pdf_name = prefix + base_name
            dest_json_name = Path(dest_pdf_name).with_suffix(".json").name

            dest_pdf = dest_folder / dest_pdf_name
            dest_json = dest_folder / dest_json_name

            if incremental and _should_skip_copy(pdf_file, dest_pdf):
                stats['skipped'] += 1
                continue

            _check_file_size(pdf_file, max_file_size_mb)
            shutil.copy2(pdf_file, dest_pdf)
            if json_file.exists():
                metadata['source_filename'] = pdf_file.name
                save_json_data(dest_json, metadata)
            stats['copied'] += 1

        if not quiet:
            console.success(f"Copied {stats['copied']} files to {dest_folder.name}", indent=False)
    else:
        matching_files = []
        for file in processed_path.iterdir():
            if not file.is_file():
                continue
            if file.suffix.lower() not in [".pdf", ".xlsx", ".json"]:
                continue
            if not matcher(file.name):
                continue
            matching_files.append(file)

        for file in console.track(matching_files, "Copying files"):
            stats['total'] += 1
            dest_file = dest_folder / file.name

            if incremental and _should_skip_copy(file, dest_file):
                stats['skipped'] += 1
                continue

            if file.suffix.lower() == '.json':
                data = load_json_data(file)
                src_ext = data.get("source_extension") or ".pdf"
                data['source_filename'] = file.with_suffix(src_ext).name
                save_json_data(dest_file, data)
            else:
                _check_file_size(file, max_file_size_mb)
                shutil.copy2(file, dest_file)
            stats['copied'] += 1

        pdf_copied = stats['copied'] // 2 if stats['copied'] > 0 else 0
        if not quiet:
            console.success(f"Copied {pdf_copied} files to {dest_folder.name}", indent=False)

    return stats
