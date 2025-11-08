from pathlib import Path
import hashlib
import sys
import fitz  # PyMuPDF

def hash_file(path: Path) -> str:
    """
    Generate content-based hash by rendering PDF pages as images.
    This detects true content duplicates even if PDF metadata differs.

    Args:
        path: Path to the PDF file

    Returns:
        First 8 characters of SHA256 digest of rendered page content
    """
    try:
        doc = fitz.open(str(path))
        page_hashes = []

        # Use deterministic rendering settings for consistency
        zoom = 150 / 72  # 150 DPI
        mat = fitz.Matrix(zoom, zoom)

        # Iterate through all pages and render each as an image
        for page_num in range(len(doc)):
            page = doc[page_num]

            try:
                # Render page as pixmap with deterministic settings
                pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
                img_data = pix.samples
                page_hash = hashlib.sha256(img_data).hexdigest()
                page_hashes.append(page_hash)
            except Exception:
                continue

        doc.close()

        # Create a digest of all page hashes combined
        if page_hashes:
            combined = "".join(page_hashes)
            content_digest = hashlib.sha256(combined.encode()).hexdigest()
            return content_digest[:8]
        else:
            # Fall back to file hash if no pages rendered
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b''): h.update(chunk)
            return h.hexdigest()[:8]

    except Exception:
        # Fall back to file hash on error
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b''): h.update(chunk)
        return h.hexdigest()[:8]

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python hash.py <file_path>")
        sys.exit(1)
    file_path = Path(sys.argv[1])
    print(hash_file(file_path))