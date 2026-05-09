"""PDF rendering, splitting, and image conversion utilities."""

import base64
import hashlib
import io
import os
import re
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

IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp', '.webp')
DOCUMENT_EXTENSIONS = ('.pdf', '.xlsx') + IMAGE_EXTENSIONS


def _normalized_extensions(extensions) -> tuple[str, ...]:
    return tuple(str(extension).lower() for extension in extensions)


def is_image_file(path: Path, image_extensions=IMAGE_EXTENSIONS) -> bool:
    """Check if a file path has an image extension."""
    return path.suffix.lower() in _normalized_extensions(image_extensions)


def _walk_folders(
    folder_paths,
    ext_set,
    *,
    skip_dirs=("logs",),
    skip_dir_prefixes=("_dupes",),
    skip_hidden_files: bool = True,
):
    """Yield matching files from one or multiple folders."""
    if isinstance(folder_paths, (str, Path)):
        folder_paths = [folder_paths]
    skip_dirs_set = set(skip_dirs or ())
    skip_prefixes = tuple(skip_dir_prefixes or ())
    for folder_path in folder_paths:
        folder_path = Path(folder_path)
        if not folder_path.exists():
            continue
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [
                directory
                for directory in dirs
                if directory not in skip_dirs_set
                and not any(directory.startswith(prefix) for prefix in skip_prefixes)
            ]
            for file in files:
                if skip_hidden_files and file.startswith('.'):
                    continue
                if not any(file.lower().endswith(e) for e in ext_set):
                    continue
                fp = Path(root) / file
                if fp.stat().st_size > 0:
                    yield fp


def find_document_files(
    folder_paths,
    extensions=DOCUMENT_EXTENSIONS,
    *,
    skip_dirs=("logs",),
    skip_dir_prefixes=("_dupes",),
    skip_hidden_files: bool = True,
) -> list[Path]:
    """Return all document files with given extensions within one or multiple folders."""
    return list(
        _walk_folders(
            folder_paths,
            {e.lower() for e in extensions},
            skip_dirs=skip_dirs,
            skip_dir_prefixes=skip_dir_prefixes,
            skip_hidden_files=skip_hidden_files,
        )
    )

_PAGINATION_PATTERNS = (r'P[aá]g\.?\s*(\d+)\s*/\s*(\d+)',)


def is_splittable_bundle(
    pdf_path: Path,
    *,
    enabled: bool = True,
    pagination_patterns=_PAGINATION_PATTERNS,
) -> bool:
    """Check if a PDF is a bundle of independent single-page documents.

    Returns True only if ALL pages have pagination matching "Pág. 1/1".
    """
    if not enabled:
        return False
    compiled_patterns = [
        re.compile(pattern)
        for pattern in pagination_patterns or _PAGINATION_PATTERNS
    ]
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return False
    try:
        if doc.page_count <= 1:
            return False
        for page in doc:
            text = page.get_text()
            match = None
            for pattern in compiled_patterns:
                match = pattern.search(text)
                if match:
                    break
            if not match:
                return False
            current, total = int(match.group(1)), int(match.group(2))
            if total != 1 or current != 1:
                return False
        return True
    finally:
        doc.close()


def split_pdf_bundle(pdf_path: Path, output_dir: Path) -> list[Path]:
    """Split a multi-page PDF bundle into individual single-page PDFs."""
    path_hash = hashlib.sha256(str(pdf_path).encode()).hexdigest()[:8]
    doc = fitz.open(pdf_path)
    output_paths = []
    try:
        for i in range(doc.page_count):
            new_doc = fitz.open()
            new_doc.insert_pdf(doc, from_page=i, to_page=i)
            output_path = output_dir / f"{pdf_path.stem}_p{i + 1}_{path_hash}.pdf"
            new_doc.save(str(output_path))
            new_doc.close()
            output_paths.append(output_path)
    finally:
        doc.close()
    logger.debug(f"[PDF-SPLIT] {pdf_path.name} -> {len(output_paths)} pages")
    return output_paths

def convert_image_to_pdf(image_path: Path, output_dir: Path) -> Path:
    """Convert a single image file to PDF."""
    path_hash = hashlib.sha256(str(image_path).encode()).hexdigest()[:8]
    output_path = output_dir / f"{image_path.stem}_{path_hash}.pdf"

    img = Image.open(image_path)
    n_frames = getattr(img, 'n_frames', 1)
    if n_frames > 1:
        frames = []
        for i in range(1, n_frames):
            img.seek(i)
            frames.append(img.copy().convert('RGB'))
        img.seek(0)
        first = img.convert('RGB')
        first.save(output_path, 'PDF', save_all=True, append_images=frames)
    else:
        img.convert('RGB').save(output_path, 'PDF')

    logger.debug(f"[IMG-CONVERT] {image_path.name} -> {output_path.name}")
    return output_path
