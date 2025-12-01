#!/usr/bin/env python3
"""
Utility to check file hash and content hash for a PDF file.
"""

import argparse
import hashlib
import sys
from pathlib import Path

import fitz  # PyMuPDF


def hash_file_fast(path: Path) -> str:
    """
    Fast file-based hash for quick duplicate detection.
    Uses raw file bytes.
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
    """
    try:
        doc = fitz.open(str(path))
        page_hashes = []

        zoom = 150 / 72
        mat = fitz.Matrix(zoom, zoom)

        for page_num in range(len(doc)):
            page = doc[page_num]
            try:
                pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
                img_data = pix.samples
                page_hash = hashlib.sha256(img_data).hexdigest()
                page_hashes.append(page_hash)
                pix = None
            except Exception:
                continue

        doc.close()

        if page_hashes:
            combined = "".join(page_hashes)
            content_digest = hashlib.sha256(combined.encode()).hexdigest()
            return content_digest[:8]
        else:
            return hash_file_fast(path)

    except Exception:
        return hash_file_fast(path)


def main():
    parser = argparse.ArgumentParser(description="Check file hash and content hash for a PDF file.")
    parser.add_argument("file_path", type=str, help="Path to the PDF file")
    args = parser.parse_args()

    path = Path(args.file_path)
    
    if not path.exists():
        print(f"Error: File not found: {path}")
        sys.exit(1)

    print(f"File: {path}")
    print(f"File hash (fast):    {hash_file_fast(path)}")
    print(f"Content hash:        {hash_file_content(path)}")


if __name__ == "__main__":
    main()

