"""Hash functions for file deduplication."""

import hashlib
import time
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from papertrail.logging_utils import get_logger
from papertrail.yaml_utils import load_yaml, save_yaml

logger = get_logger('hashing')


class HashCache:
    """Cache file_hash -> content_hash mappings to avoid recomputation."""

    def __init__(self, cache_path: Optional[Path] = None):
        if cache_path is None:
            cache_path = Path(__file__).parent.parent / ".cache" / "hash_cache.yaml"
        self.path = cache_path
        self._cache: dict[str, str] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            data = load_yaml(self.path)
            self._cache = data.get("cache", {})
        except Exception:
            self._cache = {}

    def save(self) -> None:
        if not self._dirty:
            return
        save_yaml(self.path, {"cache": self._cache})
        self._dirty = False

    def get(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def set(self, key: str, value: str) -> None:
        if self._cache.get(key) != value:
            self._cache[key] = value
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
