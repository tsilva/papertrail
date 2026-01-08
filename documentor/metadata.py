"""Metadata file operations and utilities."""

import json
import os
import re
from pathlib import Path
from typing import Iterator, Optional

from documentor.models import DocumentMetadata


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


def iter_metadata_files(
    directory: Path,
    show_progress: bool = False,
    progress_desc: str = "Processing metadata"
) -> Iterator[tuple[Path, DocumentMetadata]]:
    """
    Iterate over all metadata JSON files in a directory.

    Yields tuples of (path, metadata) for each valid metadata file.
    Invalid files are silently skipped.

    Args:
        directory: Directory to scan for JSON files
        show_progress: Whether to show a progress bar
        progress_desc: Description for the progress bar

    Yields:
        Tuples of (json_path, DocumentMetadata)
    """
    json_files = list(directory.rglob("*.json"))

    if show_progress:
        from tqdm import tqdm
        json_files = tqdm(json_files, desc=progress_desc)

    for json_path in json_files:
        try:
            metadata = load_metadata_file(json_path)
            yield json_path, metadata
        except Exception:
            # Skip invalid files
            continue


def iter_json_files(
    directory: Path,
    show_progress: bool = False,
    progress_desc: str = "Processing files"
) -> Iterator[tuple[Path, dict]]:
    """
    Iterate over all JSON files in a directory, yielding raw data.

    Yields tuples of (path, data) for each valid JSON file.
    Invalid files are silently skipped.

    Args:
        directory: Directory to scan for JSON files
        show_progress: Whether to show a progress bar
        progress_desc: Description for the progress bar

    Yields:
        Tuples of (json_path, dict)
    """
    json_files = list(directory.rglob("*.json"))

    if show_progress:
        from tqdm import tqdm
        json_files = tqdm(json_files, desc=progress_desc)

    for json_path in json_files:
        try:
            data = load_json_data(json_path)
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
    for root, _, files in os.walk(directory):
        for file in files:
            if not file.lower().endswith(".json"):
                continue
            try:
                with open(Path(root) / file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                    # Index by content hash (primary) - support both old and new field names
                    content_hash = metadata.get('content_hash') or metadata.get('hash')
                    if content_hash:
                        hash_index[content_hash] = Path(root) / file.replace(".json", ".pdf")
                    # Also index by file hash (for quick filtering)
                    file_hash = metadata.get('file_hash') or metadata.get('_old_hash')
                    if file_hash:
                        hash_index[file_hash] = Path(root) / file.replace(".json", ".pdf")
            except Exception:
                continue
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
    json_files = list(directory.rglob("*.json"))

    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            issue_date = data.get("issue_date", "")
            if issue_date and issue_date != "$UNKNOWN$":
                # Extract YYYY-MM portion
                match = re.match(r"^(\d{4}-\d{2})", issue_date)
                if match:
                    dates_set.add(match.group(1))
        except Exception:
            continue

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
