"""Hash functions for file deduplication."""

import hashlib
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
import yaml


class HashCache:
    """Cache file_hash -> content_hash mappings to avoid recomputation.

    This dramatically speeds up operations that need content hashes by
    caching the expensive content-based hash computation. Since the same
    file bytes always produce the same content hash, we can use the fast
    file hash as a cache key.
    """

    def __init__(self, cache_path: Optional[Path] = None):
        """Initialize the hash cache.

        Args:
            cache_path: Path to the YAML cache file. Defaults to config/hash_cache.yaml
        """
        if cache_path is None:
            cache_path = Path(__file__).parent.parent / "config" / "hash_cache.yaml"
        self.cache_path = cache_path
        self._cache: dict[str, str] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        """Load cache from YAML file."""
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                self._cache = data.get("cache", {})
            except Exception:
                self._cache = {}
        else:
            self._cache = {}

    def save(self) -> None:
        """Save cache to YAML file if dirty."""
        if not self._dirty:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            yaml.dump({"cache": self._cache}, f, default_flow_style=False)
        self._dirty = False

    def get(self, file_hash: str) -> Optional[str]:
        """Get cached content hash for a file hash.

        Args:
            file_hash: The fast file hash (8-char SHA256 prefix)

        Returns:
            Cached content hash if found, None otherwise
        """
        return self._cache.get(file_hash)

    def set(self, file_hash: str, content_hash: str) -> None:
        """Cache a file_hash -> content_hash mapping.

        Args:
            file_hash: The fast file hash (8-char SHA256 prefix)
            content_hash: The content-based hash (8-char SHA256 prefix)
        """
        if self._cache.get(file_hash) != content_hash:
            self._cache[file_hash] = content_hash
            self._dirty = True

    def __len__(self) -> int:
        """Return number of cached entries."""
        return len(self._cache)


def hash_file_fast(path: Path) -> str:
    """
    Fast file-based hash for quick duplicate detection.

    Uses raw file bytes - much faster than content-based hashing.

    Args:
        path: Path to the file

    Returns:
        First 8 characters of SHA256 hex digest
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()[:8]


def hash_file_content(path: Path) -> str:
    """
    Generate content-based hash by rendering PDF pages as images.

    This detects true content duplicates even if PDF metadata differs.
    Renders all pages at 150 DPI and hashes the pixel data.

    Args:
        path: Path to the PDF file

    Returns:
        First 8 characters of SHA256 digest of rendered page content
    """
    try:
        page_hashes = []

        # Use deterministic rendering settings for consistency
        # 150 DPI provides good quality while being reasonably fast
        zoom = 150 / 72  # 72 is the default DPI
        mat = fitz.Matrix(zoom, zoom)

        with fitz.open(str(path)) as doc:
            for page in doc:
                try:
                    # Render page as pixmap with deterministic settings
                    # alpha=False ensures no transparency channel for consistency
                    pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
                    page_hash = hashlib.sha256(pix.samples).hexdigest()
                    page_hashes.append(page_hash)
                except Exception:
                    # Skip pages that fail to render but continue with others
                    continue

        if not page_hashes:
            # No pages could be rendered - fall back to file hash
            return hash_file_fast(path)

        # Create a digest of all page hashes combined (in order)
        combined = "".join(page_hashes)
        return hashlib.sha256(combined.encode()).hexdigest()[:8]

    except Exception:
        # If content-based hashing fails entirely, fall back to file hash
        return hash_file_fast(path)
