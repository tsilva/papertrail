"""Metadata file operations and utilities."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator

from papertrail.console import get_console
from papertrail.models import DocumentMetadata

try:
    import orjson

    def _load_json_fast(path: Path) -> dict:
        with open(path, "rb") as f:
            return orjson.loads(f.read())

except ImportError:
    def _load_json_fast(path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def load_json_data(json_path: Path) -> dict:
    """Load raw JSON data from a file."""
    return _load_json_fast(json_path)


def iter_json_files(
    directory: Path,
    show_progress: bool = False,
    progress_desc: str = "Processing files",
    validate: bool = False
) -> Iterator[tuple[Path, DocumentMetadata | dict]]:
    """Iterate over JSON files in a directory, yielding (path, data). Invalid files are skipped."""
    json_files = list(directory.rglob("*.json"))

    iterator = get_console().track(json_files, progress_desc) if show_progress else json_files

    for json_path in iterator:
        try:
            data = load_json_data(json_path)
            if validate:
                yield json_path, DocumentMetadata.model_validate(data)
            else:
                yield json_path, data
        except Exception:
            continue


def _load_one_json_only(json_path: Path) -> tuple[Path, dict] | None:
    """Load a single JSON file, returning None on error. Safe for ThreadPoolExecutor."""
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
    """Load all JSON files in parallel (I/O phase) then validate sequentially (CPU phase)."""
    json_files = list(directory.rglob("*.json"))
    if not json_files:
        return []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        raw_results = list(executor.map(_load_one_json_only, json_files))

    loaded = [r for r in raw_results if r is not None]

    if not validate:
        return loaded

    results = []
    iterator = get_console().track(loaded, progress_desc) if show_progress else loaded

    for json_path, data in iterator:
        try:
            metadata = DocumentMetadata.model_validate(data)
            results.append((json_path, metadata))
        except Exception:
            continue

    return results


def build_hash_index(directory: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    """Build (content_hash_index, file_hash_index) from metadata files."""
    content_hash_index = {}
    file_hash_index = {}

    for json_path, data in iter_json_files(directory):
        pdf_path = json_path.with_suffix(".pdf")
        content_hash = data.get('hash_content')
        if content_hash:
            content_hash_index[content_hash] = pdf_path
        file_hash = data.get('hash_file')
        if file_hash:
            file_hash_index[file_hash] = pdf_path

    return content_hash_index, file_hash_index


def get_unique_dates(directory: Path) -> list[str]:
    """Extract unique YYYY-MM dates from metadata files, sorted most recent first."""
    dates_set = set()

    for _, data in iter_json_files(directory):
        issue_date = data.get("date_issued", "")
        if issue_date and issue_date != "$UNKNOWN$":
            match = re.match(r"^(\d{4}-\d{2})", issue_date)
            if match:
                dates_set.add(match.group(1))

    return sorted(dates_set, reverse=True)


def save_json_data(json_path: Path, data: dict) -> None:
    """Save dict to JSON with consistent formatting."""
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False, sort_keys=True)


def save_metadata_json(pdf_path: Path, metadata: DocumentMetadata) -> None:
    """Save metadata JSON alongside a PDF file."""
    save_json_data(pdf_path.with_suffix('.json'), metadata.model_dump())


def load_validated_metadata(
    directory: Path,
    require_pdf: bool = True,
    validate: bool = False,
    show_progress: bool = False,
    progress_desc: str = "Loading metadata"
) -> Iterator[tuple[Path, Path, DocumentMetadata | dict]]:
    """Iterate metadata files, yielding (json_path, pdf_path, data). Skips orphans if require_pdf."""
    json_files = list(directory.rglob("*.json"))
    if not json_files:
        return

    iterator = get_console().track(json_files, progress_desc) if show_progress else json_files

    for json_path in iterator:
        pdf_path = json_path.with_suffix(".pdf")

        if require_pdf and not pdf_path.exists():
            continue

        try:
            data = _load_json_fast(json_path)
            if validate:
                yield json_path, pdf_path, DocumentMetadata.model_validate(data)
            else:
                yield json_path, pdf_path, data
        except Exception:
            continue
