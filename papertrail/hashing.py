"""Hash functions for file deduplication."""

import hashlib
from pathlib import Path

import fitz  # PyMuPDF


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
