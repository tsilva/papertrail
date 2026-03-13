"""Filesystem-backed document storage helpers."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator

from papertrail.app import App, get_app
from papertrail.models import DocumentMetadata
from papertrail.naming import file_name_from_metadata

try:
    import orjson

    def _load_json_fast(path: Path) -> dict:
        with open(path, "rb") as handle:
            return orjson.loads(handle.read())

except ImportError:

    def _load_json_fast(path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)


class DocumentStore:
    """Filesystem-backed store for sidecars and companion documents."""

    def __init__(self, app: App):
        self.app = app

    @staticmethod
    def is_internal_path(path: Path) -> bool:
        """Check if a path should be excluded from document scans."""
        parts = path.parts
        return (
            any(part.startswith("_dupes") for part in parts)
            or "logs" in parts
            or path.name.startswith("_")
        )

    def resolve_scope(self, scope: str | Path = "processed") -> Path:
        """Resolve a logical scope to a concrete directory."""
        if isinstance(scope, Path):
            return scope
        if scope == "processed":
            return self.app.require_processed_path()
        if scope == "export":
            return self.app.require_export_path()
        raise ValueError(f"Unsupported document scope: {scope}")

    def find_companion(self, json_path: Path, metadata: dict | None = None) -> Path | None:
        """Find a companion file for a JSON sidecar."""
        if metadata:
            ext = metadata.get("source_extension")
            if ext:
                candidate = json_path.with_suffix(ext)
                if candidate.exists():
                    return candidate
        for ext in (".pdf", ".xlsx"):
            candidate = json_path.with_suffix(ext)
            if candidate.exists():
                return candidate
        return None

    def load_metadata(self, json_path: Path, validate: bool = False) -> DocumentMetadata | dict:
        """Load metadata from a sidecar file."""
        data = _load_json_fast(json_path)
        if validate:
            return DocumentMetadata.model_validate(data)
        return data

    def save_json(self, json_path: Path, data: dict) -> None:
        """Persist raw JSON data."""
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=4, ensure_ascii=False, sort_keys=True)

    def save_document(self, doc_path: Path, metadata: DocumentMetadata) -> None:
        """Persist a sidecar alongside a document file."""
        self.save_json(doc_path.with_suffix(".json"), metadata.model_dump())

    def iter_sidecars(
        self,
        scope: str | Path = "processed",
        validate: bool = False,
        show_progress: bool = False,
        progress_desc: str = "Processing files",
    ) -> Iterator[tuple[Path, DocumentMetadata | dict]]:
        """Iterate sidecars in a scope."""
        root = self.resolve_scope(scope)
        json_files = [path for path in root.rglob("*.json") if not self.is_internal_path(path.relative_to(root))]
        iterator = self.app.console.track(json_files, progress_desc) if show_progress else json_files

        for json_path in iterator:
            try:
                yield json_path, self.load_metadata(json_path, validate=validate)
            except Exception:
                continue

    def load_sidecars_parallel(
        self,
        scope: str | Path = "processed",
        validate: bool = False,
        max_workers: int = 16,
        show_progress: bool = False,
        progress_desc: str = "Loading metadata",
    ) -> list[tuple[Path, DocumentMetadata | dict]]:
        """Load sidecars in parallel."""
        root = self.resolve_scope(scope)
        json_files = [path for path in root.rglob("*.json") if not self.is_internal_path(path.relative_to(root))]
        if not json_files:
            return []

        def _load_one(json_path: Path) -> tuple[Path, dict] | None:
            try:
                return json_path, _load_json_fast(json_path)
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            raw_results = list(executor.map(_load_one, json_files))

        loaded = [result for result in raw_results if result is not None]
        if not validate:
            return loaded

        iterator = self.app.console.track(loaded, progress_desc) if show_progress else loaded
        results: list[tuple[Path, DocumentMetadata]] = []
        for json_path, data in iterator:
            try:
                results.append((json_path, DocumentMetadata.model_validate(data)))
            except Exception:
                continue
        return results

    def iter_documents(
        self,
        scope: str | Path = "processed",
        validate: bool = False,
        require_companion: bool = True,
        show_progress: bool = False,
        progress_desc: str = "Loading metadata",
    ) -> Iterator[tuple[Path, Path, DocumentMetadata | dict]]:
        """Iterate sidecars and their companion files."""
        for json_path, metadata in self.iter_sidecars(
            scope=scope,
            validate=validate,
            show_progress=show_progress,
            progress_desc=progress_desc,
        ):
            metadata_dict = metadata.model_dump() if isinstance(metadata, DocumentMetadata) else metadata
            doc_path = self.find_companion(json_path, metadata_dict) or json_path.with_suffix(".pdf")
            if require_companion and not doc_path.exists():
                continue
            yield json_path, doc_path, metadata

    def build_indexes(
        self,
        scope: str | Path = "processed",
    ) -> tuple[dict[str, Path], dict[str, Path], dict[str, Path], set[str]]:
        """Build content/file/text hash indexes and the known issuer set."""
        content_hash_index: dict[str, Path] = {}
        file_hash_index: dict[str, Path] = {}
        text_hash_index: dict[str, Path] = {}
        known_issuers: set[str] = set()

        for json_path, data in self.iter_sidecars(scope=scope):
            doc_path = self.find_companion(json_path, data) or json_path.with_suffix(".pdf")
            content_hash = data.get("hash_content")
            if content_hash:
                content_hash_index[content_hash] = doc_path
            file_hash = data.get("hash_file")
            if file_hash:
                file_hash_index[file_hash] = doc_path
            text_hash = data.get("hash_text")
            if text_hash:
                text_hash_index[text_hash] = doc_path
            issuing_party = data.get("issuing_party")
            if issuing_party and issuing_party != "$UNKNOWN$":
                known_issuers.add(issuing_party)

        return content_hash_index, file_hash_index, text_hash_index, known_issuers

    def unique_dates(self, scope: str | Path = "processed") -> list[str]:
        """Return unique YYYY-MM dates from document metadata."""
        dates_set: set[str] = set()
        for _, data in self.iter_sidecars(scope=scope):
            issue_date = data.get("date_issued", "")
            if issue_date and issue_date != "$UNKNOWN$":
                match = re.match(r"^(\d{4}-\d{2})", issue_date)
                if match:
                    dates_set.add(match.group(1))
        return sorted(dates_set, reverse=True)

    def repair_filenames(self, scope: str | Path = "processed") -> dict:
        """Rename existing document files to match their metadata."""
        root = self.resolve_scope(scope)
        valid_entries: list[tuple[Path, DocumentMetadata]] = []
        orphan_count = 0

        for metadata_path, metadata in self.load_sidecars_parallel(
            root,
            validate=True,
            show_progress=False,
        ):
            doc_path = self.find_companion(metadata_path, metadata.model_dump())
            if doc_path is None:
                orphan_count += 1
                continue
            valid_entries.append((doc_path, metadata))

        renamed_count = 0
        for old_doc_path, metadata in valid_entries:
            new_filename = file_name_from_metadata(metadata, metadata.hash_file)
            new_doc_path = root / new_filename
            new_metadata_path = new_doc_path.with_suffix(".json")
            if old_doc_path == new_doc_path:
                continue

            old_metadata_path = old_doc_path.with_suffix(".json")
            old_doc_path.rename(new_doc_path)
            old_metadata_path.rename(new_metadata_path)
            renamed_count += 1

        return {
            "validated": len(valid_entries),
            "renamed": renamed_count,
            "orphans": orphan_count,
        }

    def archive_by_hash_file(
        self,
        digests: list[str],
        dry_run: bool = False,
        scope: str | Path = "processed",
    ) -> dict:
        """Archive documents by hash_file digest."""
        root = self.resolve_scope(scope)
        archive_dir = root.parent / "_archived"
        hash_to_json: dict[str, Path] = {}
        for json_path, data in self.iter_sidecars(scope=root):
            hash_file = data.get("hash_file")
            if hash_file:
                hash_to_json[hash_file] = json_path

        if not dry_run:
            archive_dir.mkdir(exist_ok=True)

        found = 0
        moved = 0
        not_found: list[str] = []
        for digest in digests:
            json_path = hash_to_json.get(digest)
            if not json_path:
                not_found.append(digest)
                continue
            found += 1
            data = None
            try:
                data = self.load_metadata(json_path)
            except Exception:
                pass

            files_to_move = [json_path]
            companion = self.find_companion(json_path, data)
            if companion and companion.exists():
                files_to_move.append(companion)

            stem = json_path.stem
            for extra_suffix in (".embeddings.json", ".reconciliation.json"):
                extra = json_path.parent / f"{stem}{extra_suffix}"
                if extra.exists():
                    files_to_move.append(extra)

            for src in files_to_move:
                dst = archive_dir / src.name
                if dst.exists():
                    base, suffix = dst.stem, dst.suffix
                    counter = 2
                    while dst.exists():
                        dst = archive_dir / f"{base}_{counter}{suffix}"
                        counter += 1
                if not dry_run:
                    src.rename(dst)
            moved += 1

        return {
            "archive_dir": archive_dir,
            "found": found,
            "archived": moved,
            "not_found": not_found,
        }


def get_store() -> DocumentStore:
    """Return a store for the active app."""
    return DocumentStore(get_app())
