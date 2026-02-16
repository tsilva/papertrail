"""PDF rendering, splitting, and image conversion utilities."""

import hashlib
import io
import os
import re
import base64
import time
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageEnhance

from papertrail.logging_utils import get_logger

logger = get_logger('pdf')


# ── Rendering ────────────────────────────────────────────────────

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


# ── File discovery ───────────────────────────────────────────────

IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp', '.webp')


def is_image_file(path: Path) -> bool:
    """Check if a file path has an image extension."""
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _walk_folders(folder_paths, ext_set):
    """Yield matching files from one or multiple folders."""
    if isinstance(folder_paths, (str, Path)):
        folder_paths = [folder_paths]
    for folder_path in folder_paths:
        folder_path = Path(folder_path)
        if not folder_path.exists():
            continue
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith("_dupes") and d != "logs"]
            for file in files:
                if file.startswith('.'):
                    continue
                if not any(file.lower().endswith(e) for e in ext_set):
                    continue
                fp = Path(root) / file
                if fp.stat().st_size > 0:
                    yield fp


def find_pdf_files(folder_paths) -> list[Path]:
    """Return all PDF files within one or multiple folders."""
    return list(_walk_folders(folder_paths, {'.pdf'}))


def find_document_files(folder_paths, extensions=('.pdf', '.xlsx') + IMAGE_EXTENSIONS) -> list[Path]:
    """Return all document files with given extensions within one or multiple folders."""
    return list(_walk_folders(folder_paths, {e.lower() for e in extensions}))


# ── Bundle splitting ─────────────────────────────────────────────

_PAGINATION_RE = re.compile(r'P[aá]g\.?\s*(\d+)\s*/\s*(\d+)')


def is_splittable_bundle(pdf_path: Path) -> bool:
    """Check if a PDF is a bundle of independent single-page documents.

    Returns True only if ALL pages have pagination matching "Pág. 1/1".
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return False
    try:
        if doc.page_count <= 1:
            return False
        for page in doc:
            match = _PAGINATION_RE.search(page.get_text())
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


def split_pdf_bundles(
    pdf_paths: list[Path], output_dir: Path, console,
) -> tuple[list[Path], list[Path], int]:
    """Split splittable PDF bundles, pass through the rest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    non_splittable = []
    split_pages = []
    bundles_split = 0

    for pdf_path in console.track(pdf_paths, "Detecting bundles"):
        if is_splittable_bundle(pdf_path):
            try:
                pages = split_pdf_bundle(pdf_path, output_dir)
                split_pages.extend(pages)
                bundles_split += 1
            except Exception as e:
                logger.warning(f"[PDF-SPLIT] Failed to split {pdf_path.name}: {e}")
                non_splittable.append(pdf_path)
        else:
            non_splittable.append(pdf_path)

    return non_splittable, split_pages, bundles_split


# ── Image conversion ─────────────────────────────────────────────

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


def convert_images_to_pdfs(image_paths: list[Path], output_dir: Path, console) -> list[Path]:
    """Convert multiple image files to PDFs. Returns list of converted PDF paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    converted = []
    for image_path in console.track(image_paths, "Converting images"):
        try:
            pdf_path = convert_image_to_pdf(image_path, output_dir)
            converted.append(pdf_path)
        except Exception as e:
            logger.warning(f"[IMG-CONVERT] Failed to convert {image_path.name}: {e}")
    return converted
