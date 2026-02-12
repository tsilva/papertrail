"""PDF rendering utilities."""

import io
import os
import base64
import time
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageEnhance

from papertrail.logging_utils import get_logger

logger = get_logger('pdf')


def render_pdf_to_images(
    pdf_path: Path,
    max_pages: int = 2,
    enhance_contrast: bool = True,
    contrast_factor: float = 2.0
) -> list[str]:
    """Render PDF pages to base64-encoded JPEG images."""
    t0 = time.monotonic()
    images_b64 = []

    with fitz.open(str(pdf_path)) as doc:
        total_pages = len(doc)
        num_pages = min(max_pages, total_pages)
        logger.debug(f"[PDF-RENDER] {pdf_path.name}: {total_pages} pages, rendering {num_pages}")

        for i in range(num_pages):
            page = doc[i]
            pix = page.get_pixmap()
            img = Image.open(io.BytesIO(pix.tobytes("jpeg")))

            if enhance_contrast:
                enhancer = ImageEnhance.Contrast(img)
                img = enhancer.enhance(contrast_factor)

            img_buffer = io.BytesIO()
            img.save(img_buffer, format="JPEG")
            img_b64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")
            images_b64.append(img_b64)

    elapsed = time.monotonic() - t0
    logger.debug(f"[PDF-RENDER] {pdf_path.name}: completed in {elapsed:.2f}s")
    return images_b64


def get_page_count(pdf_path: Path) -> int:
    """Return the number of pages in a PDF file."""
    with fitz.open(str(pdf_path)) as doc:
        return len(doc)


def find_pdf_files(folder_paths) -> list[Path]:
    """Return all PDF files within one or multiple folders."""
    if isinstance(folder_paths, (str, Path)):
        folder_paths = [folder_paths]

    pdfs = []
    for folder_path in folder_paths:
        folder_path = Path(folder_path)
        if not folder_path.exists():
            continue
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith("_dupes") and d != "logs"]
            for file in files:
                if (
                    file.lower().endswith('.pdf')
                    and not file.startswith('.')
                    and (Path(root) / file).stat().st_size > 0
                ):
                    pdfs.append(Path(root) / file)
    return pdfs


def find_document_files(folder_paths, extensions=('.pdf', '.xlsx')) -> list[Path]:
    """Return all document files with given extensions within one or multiple folders."""
    if isinstance(folder_paths, (str, Path)):
        folder_paths = [folder_paths]

    ext_set = {e.lower() for e in extensions}
    docs = []
    for folder_path in folder_paths:
        folder_path = Path(folder_path)
        if not folder_path.exists():
            continue
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith("_dupes") and d != "logs"]
            for file in files:
                if (
                    file.startswith('.')
                    or not any(file.lower().endswith(e) for e in ext_set)
                ):
                    continue
                fp = Path(root) / file
                if fp.stat().st_size > 0:
                    docs.append(fp)
    return docs
