"""Split multi-note PDF bundles into individual single-page documents."""

import hashlib
import re
from pathlib import Path

import fitz

from papertrail.logging_utils import get_logger

logger = get_logger('pdf_split')

_PAGINATION_RE = re.compile(r'P[aá]g\.?\s*(\d+)\s*/\s*(\d+)')


def is_splittable_bundle(pdf_path: Path) -> bool:
    """Check if a PDF is a bundle of independent single-page documents.

    Returns True only if ALL pages have pagination matching "Pág. 1/1"
    (each page is a self-contained document). Returns False for genuine
    multi-page documents (sequential pagination like "Pág. 1/4").
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return False

    try:
        if doc.page_count <= 1:
            return False

        for page in doc:
            text = page.get_text()
            match = _PAGINATION_RE.search(text)
            if not match:
                return False
            current, total = int(match.group(1)), int(match.group(2))
            if total != 1 or current != 1:
                return False

        return True
    finally:
        doc.close()


def split_pdf_bundle(pdf_path: Path, output_dir: Path) -> list[Path]:
    """Split a multi-page PDF bundle into individual single-page PDFs.

    Output filenames use {stem}_p{N}_{path_hash}.pdf to avoid collisions.
    """
    path_hash = hashlib.sha256(str(pdf_path).encode()).hexdigest()[:8]
    doc = fitz.open(pdf_path)
    output_paths = []

    try:
        for i in range(doc.page_count):
            new_doc = fitz.open()
            new_doc.insert_pdf(doc, from_page=i, to_page=i)
            output_name = f"{pdf_path.stem}_p{i + 1}_{path_hash}.pdf"
            output_path = output_dir / output_name
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
    """Split splittable PDF bundles, pass through the rest.

    Returns (non_splittable_paths, split_page_paths, bundles_split_count).
    """
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
