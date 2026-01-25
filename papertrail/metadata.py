"""Metadata file operations and utilities."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator

from papertrail.models import DocumentMetadata

# Use orjson for faster JSON parsing (3-10x faster than stdlib json)
try:
    import orjson

    def _load_json_fast(path: Path) -> dict:
        """Load JSON using orjson (fast, releases GIL)."""
        with open(path, "rb") as f:
            return orjson.loads(f.read())

except ImportError:
    def _load_json_fast(path: Path) -> dict:
        """Fallback to stdlib json if orjson not available."""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def load_metadata_file(json_path: Path) -> DocumentMetadata:
    """
    Load and validate metadata from a JSON file.

    Args:
        json_path: Path to the JSON metadata file

    Returns:
        Validated DocumentMetadata instance

    Raises:
        Various validation errors if the file is invalid
    """
    data = _load_json_fast(json_path)
    return DocumentMetadata.model_validate(data)


def load_json_data(json_path: Path) -> dict:
    """
    Load raw JSON data from a file.

    Args:
        json_path: Path to the JSON file

    Returns:
        Dictionary with the JSON data
    """
    return _load_json_fast(json_path)


def iter_json_files(
    directory: Path,
    show_progress: bool = False,
    progress_desc: str = "Processing files",
    validate: bool = False
) -> Iterator[tuple[Path, DocumentMetadata | dict]]:
    """
    Iterate over all JSON files in a directory.

    Yields tuples of (path, data) for each valid JSON file.
    Invalid files are silently skipped.

    Args:
        directory: Directory to scan for JSON files
        show_progress: Whether to show a progress bar
        progress_desc: Description for the progress bar
        validate: If True, yield (path, DocumentMetadata). If False, yield (path, dict).

    Yields:
        Tuples of (json_path, DocumentMetadata | dict)
    """
    json_files = list(directory.rglob("*.json"))

    if show_progress:
        from tqdm import tqdm
        json_files = tqdm(json_files, desc=progress_desc)

    for json_path in json_files:
        try:
            data = load_json_data(json_path)
            if validate:
                yield json_path, DocumentMetadata.model_validate(data)
            else:
                yield json_path, data
        except Exception:
            continue


def _load_one_json_only(json_path: Path) -> tuple[Path, dict] | None:
    """Load a single JSON file without validation, returning None on error.

    Uses orjson for fast parsing. Safe for ThreadPoolExecutor since orjson
    releases the GIL during parsing.
    """
    try:
        data = _load_json_fast(json_path)
        return json_path, data
    except Exception:
        return None


def load_json_files_parallel(
    directory: Path,
    validate: bool = False,
    max_workers: int = 16,
    show_progress: bool = False,
    progress_desc: str = "Loading metadata"
) -> list[tuple[Path, DocumentMetadata | dict]]:
    """
    Load all JSON files in parallel using ThreadPoolExecutor + orjson.

    Uses a two-phase approach when validation is needed:
    1. Phase 1: Load all JSON files in parallel (I/O bound, threads work well)
    2. Phase 2: Validate sequentially (CPU bound, GIL prevents thread parallelism)

    This is faster than validating in threads because Pydantic validation
    is CPU-bound and can't be parallelized with threads.

    Args:
        directory: Directory to scan for JSON files
        validate: If True, return (path, DocumentMetadata). If False, return (path, dict).
        max_workers: Maximum number of parallel threads (default: 16)
        show_progress: Whether to show a progress bar
        progress_desc: Description for the progress bar

    Returns:
        List of tuples (json_path, DocumentMetadata | dict)
    """
    json_files = list(directory.rglob("*.json"))
    if not json_files:
        return []

    # Phase 1: Load all JSON files in parallel (I/O bound - threads work well)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        raw_results = list(executor.map(_load_one_json_only, json_files))

    # Filter out failures
    loaded = [r for r in raw_results if r is not None]

    if not validate:
        return loaded

    # Phase 2: Validate sequentially (CPU bound - GIL prevents thread parallelism)
    results = []
    iterator = loaded
    if show_progress:
        from tqdm import tqdm
        iterator = tqdm(loaded, desc=progress_desc)

    for json_path, data in iterator:
        try:
            metadata = DocumentMetadata.model_validate(data)
            results.append((json_path, metadata))
        except Exception:
            continue

    return results


def build_hash_index(directory: Path) -> dict[str, Path]:
    """
    Build index of both content hashes and file hashes from metadata files.

    Args:
        directory: Directory containing metadata JSON files

    Returns:
        Dictionary mapping hash -> PDF path
    """
    hash_index = {}

    for json_path, data in iter_json_files(directory):
        pdf_path = json_path.with_suffix(".pdf")
        # Index by content hash (primary) - support both old and new field names
        content_hash = data.get('content_hash') or data.get('hash')
        if content_hash:
            hash_index[content_hash] = pdf_path
        # Also index by file hash (for quick filtering)
        file_hash = data.get('file_hash') or data.get('_old_hash')
        if file_hash:
            hash_index[file_hash] = pdf_path

    return hash_index


def get_unique_dates(directory: Path) -> list[str]:
    """
    Scan all JSON metadata files and extract unique YYYY-MM dates.

    Args:
        directory: Directory containing metadata JSON files

    Returns:
        Sorted list of dates (most recent first)
    """
    dates_set = set()

    for _, data in iter_json_files(directory):
        issue_date = data.get("issue_date", "")
        if issue_date and issue_date != "$UNKNOWN$":
            # Extract YYYY-MM portion
            match = re.match(r"^(\d{4}-\d{2})", issue_date)
            if match:
                dates_set.add(match.group(1))

    # Sort dates in descending order (most recent first)
    return sorted(dates_set, reverse=True)


def save_metadata_json(pdf_path: Path, metadata: DocumentMetadata) -> None:
    """
    Save metadata JSON alongside a PDF file.

    Args:
        pdf_path: Path to the PDF file
        metadata: DocumentMetadata instance to save
    """
    json_path = pdf_path.with_suffix('.json')
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata.model_dump(by_alias=True), f, indent=4)


def get_field_with_aliases(data: dict, field_name: str, aliases: list[str]):
    """
    Get field value, falling back to aliases if needed.

    Useful for handling field name migrations (e.g., 'content_hash' vs 'hash').

    Args:
        data: Dictionary to search
        field_name: Primary field name
        aliases: List of alternative field names to try

    Returns:
        Field value or None if not found
    """
    if field_name in data:
        return data[field_name]
    for alias in aliases:
        if alias in data:
            return data[alias]
    return None
