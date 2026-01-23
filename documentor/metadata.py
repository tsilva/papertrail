"""Metadata file operations and utilities."""

import json
import re
from pathlib import Path
from typing import Iterator

from papertrail.models import DocumentMetadata


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
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return DocumentMetadata.model_validate(data)


def load_json_data(json_path: Path) -> dict:
    """
    Load raw JSON data from a file.

    Args:
        json_path: Path to the JSON file

    Returns:
        Dictionary with the JSON data
    """
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


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
