"""PDF rendering utilities."""

import io
import os
import base64
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageEnhance


def render_pdf_to_images(
    pdf_path: Path,
    max_pages: int = 2,
    enhance_contrast: bool = True,
    contrast_factor: float = 2.0
) -> list[str]:
    """
    Render PDF pages to base64 encoded JPEG images.

    Args:
        pdf_path: Path to the PDF file
        max_pages: Maximum number of pages to render (default: 2)
        enhance_contrast: Whether to apply contrast enhancement (default: True)
        contrast_factor: Contrast enhancement factor (default: 2.0)

    Returns:
        List of base64-encoded JPEG images
    """
    doc = fitz.open(str(pdf_path))
    images_b64 = []
    num_pages = min(max_pages, len(doc))

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

    doc.close()
    return images_b64


def get_page_count(pdf_path: Path) -> int:
    """Return the number of pages in a PDF file."""
    doc = fitz.open(str(pdf_path))
    count = len(doc)
    doc.close()
    return count


def find_pdf_files(folder_paths) -> list[Path]:
    """
    Return all PDF files within one or multiple folders.

    Args:
        folder_paths: Single path or list of paths to search

    Returns:
        List of paths to PDF files
    """
    if isinstance(folder_paths, (str, Path)):
        folder_paths = [folder_paths]

    pdfs = []
    for folder_path in folder_paths:
        folder_path = Path(folder_path)
        if not folder_path.exists():
            continue
        for root, _, files in os.walk(folder_path):
            for file in files:
                if (
                    file.lower().endswith('.pdf')
                    and not file.startswith('.')
                    and (Path(root) / file).stat().st_size > 0
                ):
                    pdfs.append(Path(root) / file)
    return pdfs
