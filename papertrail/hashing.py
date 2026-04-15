"""Hash functions and deduplication for file processing."""

from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from papertrail.logging_utils import get_logger
from papertrail.utils import load_yaml, save_yaml

logger = get_logger('hashing')
_CACHE_LOAD_EXCEPTIONS = (OSError, UnicodeDecodeError, ValueError)


class HashCache:
    """Cache file_hash -> content_hash and file_hash -> text_hash mappings."""

    def __init__(self, cache_path: Optional[Path] = None):
        if cache_path is None:
            cache_path = Path(__file__).parent.parent / ".cache" / "hash_cache.yaml"
        self.path = cache_path
        self._cache: dict[str, str] = {}
        self._text_cache: dict[str, str] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            data = load_yaml(self.path)
            self._cache = data.get("cache", {})
            self._text_cache = data.get("text_cache", {})
        except _CACHE_LOAD_EXCEPTIONS:
            self._cache = {}
            self._text_cache = {}

    def save(self) -> None:
        if not self._dirty:
            return
        save_yaml(self.path, {"cache": self._cache, "text_cache": self._text_cache})
        self._dirty = False

    def get(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def set(self, key: str, value: str) -> None:
        if self._cache.get(key) != value:
            self._cache[key] = value
            self._dirty = True

    def get_text(self, key: str) -> Optional[str]:
        return self._text_cache.get(key)

    def set_text(self, key: str, value: str) -> None:
        if self._text_cache.get(key) != value:
            self._text_cache[key] = value
            self._dirty = True

    def __len__(self) -> int:
        return len(self._cache)


def hash_file_fast(path: Path) -> str:
    """Fast SHA256 hash of raw file bytes (first 8 hex chars)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()[:8]


def hash_file_content(path: Path) -> str:
    """Content-based hash: renders all pages at 150 DPI, hashes pixels (first 8 hex chars)."""
    t0 = time.monotonic()
    try:
        page_hashes = []
        zoom = 150 / 72
        mat = fitz.Matrix(zoom, zoom)

        with fitz.open(str(path)) as doc:
            num_pages = len(doc)
            for page in doc:
                try:
                    pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
                    page_hash = hashlib.sha256(pix.samples).hexdigest()
                    page_hashes.append(page_hash)
                except Exception:
                    continue

        if not page_hashes:
            logger.warning(f"No pages rendered for {path.name}, falling back to file hash")
            return hash_file_fast(path)

        combined = "".join(page_hashes)
        result = hashlib.sha256(combined.encode()).hexdigest()[:8]
        elapsed = time.monotonic() - t0
        logger.debug(f"[HASH] {path.name}: {num_pages} pages in {elapsed:.2f}s")
        return result

    except Exception as e:
        logger.warning(f"Content hashing failed for {path.name} ({e}), falling back to file hash")
        return hash_file_fast(path)


def _page_has_text(page) -> bool:
    """Check if a PDF page has meaningful extractable text (>= 50 chars)."""
    text = page.get_text().strip()
    return len(text) >= 50


def _normalize_text_for_hash(pages_text: list[str]) -> str:
    """Aggressively normalize text for hashing: lowercase, ASCII-only, no whitespace."""
    combined = "".join(pages_text)
    combined = combined.lower()
    nfkd = unicodedata.normalize("NFKD", combined)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return "".join(ascii_text.split())


def hash_file_text(path: Path) -> Optional[str]:
    """Text-based hash: extracts text from all pages, normalizes, SHA256 (first 8 hex chars).

    Returns None if any page lacks extractable text (e.g., scanned/image-only PDFs).
    """
    t0 = time.monotonic()
    try:
        with fitz.open(str(path)) as doc:
            num_pages = len(doc)
            if num_pages == 0:
                return None

            pages_text = []
            for page in doc:
                if not _page_has_text(page):
                    logger.debug(f"[HASH-TEXT] {path.name}: page {page.number} has insufficient text, skipping")
                    return None
                pages_text.append(page.get_text())

        normalized = _normalize_text_for_hash(pages_text)
        if not normalized:
            return None

        result = hashlib.sha256(normalized.encode()).hexdigest()[:8]
        elapsed = time.monotonic() - t0
        logger.debug(f"[HASH-TEXT] {path.name}: {num_pages} pages in {elapsed:.3f}s -> {result}")
        return result

    except Exception as e:
        logger.debug(f"[HASH-TEXT] Failed for {path.name}: {e}")
        return None


# --- Deduplication utilities ---

def group_duplicates(file_records: list[dict]) -> list[dict]:
    """Group file records into duplicate sets using three-tier hash hierarchy.

    Primary grouping uses ``hash_content``. Files without it fall back to ``hash_text``.
    Within each group, entries sorted by ``size_kb`` ascending (smallest first = "keep").
    """
    content_groups: dict[str, list[dict]] = {}
    text_only_groups: dict[str, list[dict]] = {}

    for record in file_records:
        content_hash = record.get("hash_content")
        text_hash = record.get("hash_text")
        if content_hash:
            content_groups.setdefault(content_hash, []).append(record)
        elif text_hash:
            text_only_groups.setdefault(text_hash, []).append(record)

    groups = []
    for hash_val, entries in sorted(content_groups.items()):
        if len(entries) >= 2:
            entries.sort(key=lambda e: e.get("size_kb") or 0)
            groups.append({"group_hash": hash_val, "group_hash_type": "hash_content", "entries": entries})
    for hash_val, entries in sorted(text_only_groups.items()):
        if len(entries) >= 2:
            entries.sort(key=lambda e: e.get("size_kb") or 0)
            groups.append({"group_hash": hash_val, "group_hash_type": "hash_text", "entries": entries})
    return groups
PLAN_FILENAME = "_dupes_plan.json"


def scan_directory(directory: Path) -> dict:
    """Scan directory for duplicate files and return a deduplication plan dict.

    Groups files by hash_content (primary) or hash_text (fallback).
    Each group has a `decision` field (initially None).
    """
    json_files = sorted(directory.rglob("*.json"))

    file_records: list[dict] = []
    scanned = 0
    skipped_no_hash = 0

    for json_path in json_files:
        if "/logs/" in str(json_path) or json_path.name.startswith("_"):
            continue
        if json_path.name.endswith(".reconciliation.json"):
            continue
        if any(part.startswith("_dupes") for part in json_path.parts):
            continue

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue

        scanned += 1

        content_hash = data.get("hash_content")
        text_hash = data.get("hash_text")
        if not text_hash:
            # Try to compute for PDFs
            ext = data.get("source_extension")
            companion = json_path.with_suffix(ext) if ext else None
            if companion is None or not companion.exists():
                for fb_ext in (".pdf", ".xlsx"):
                    candidate = json_path.with_suffix(fb_ext)
                    if candidate.exists():
                        companion = candidate
                        break
            if companion and companion.suffix.lower() == ".pdf":
                text_hash = hash_file_text(companion)

        if not content_hash and not text_hash:
            skipped_no_hash += 1
            continue

        size_kb = data.get("file_size_kb")
        if size_kb is None:
            ext = data.get("source_extension")
            companion = json_path.with_suffix(ext) if ext else None
            if companion is None or not companion.exists():
                for fb_ext in (".pdf", ".xlsx"):
                    candidate = json_path.with_suffix(fb_ext)
                    if candidate.exists():
                        companion = candidate
                        break
            if companion and companion.exists():
                size_kb = round(companion.stat().st_size / 1024)

        file_records.append({
            "json": json_path.name,
            "json_path": str(json_path),
            "size_kb": size_kb,
            "hash_content": content_hash,
            "hash_text": text_hash,
        })

    raw_groups = group_duplicates(file_records)

    dupe_groups = []
    total_files_to_move = 0
    space_savings_kb = 0

    for g in raw_groups:
        entries = g["entries"]
        keep = entries[0]
        move = entries[1:]

        group = {
            "group_hash": g["group_hash"],
            "group_hash_type": g["group_hash_type"],
            "decision": None,
            "keep": {
                "json": keep["json"],
                "size_kb": keep["size_kb"],
                "hash_content": keep.get("hash_content"),
            },
            "move": [
                {
                    "json": m["json"],
                    "size_kb": m["size_kb"],
                    "hash_content": m.get("hash_content"),
                }
                for m in move
            ],
        }
        dupe_groups.append(group)
        total_files_to_move += len(move)
        space_savings_kb += sum((m["size_kb"] or 0) for m in move)

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "directory": str(directory),
        "scan_stats": {
            "scanned": scanned,
            "skipped_no_hash": skipped_no_hash,
        },
        "summary": {
            "total_groups": len(dupe_groups),
            "total_files_to_move": total_files_to_move,
            "space_savings_kb": space_savings_kb,
            "approved": 0,
            "rejected": 0,
            "pending": len(dupe_groups),
        },
        "groups": dupe_groups,
    }
