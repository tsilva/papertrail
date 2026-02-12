"""Convert image files to PDF for processing through the classification pipeline."""

import hashlib
from pathlib import Path

from PIL import Image

from papertrail.logging_utils import get_logger

logger = get_logger('image_convert')


def convert_image_to_pdf(image_path: Path, output_dir: Path) -> Path:
    """Convert a single image file to PDF.

    Output filename uses {stem}_{path_hash}.pdf to avoid collisions
    from same-named images in different raw directories.
    """
    path_hash = hashlib.sha256(str(image_path).encode()).hexdigest()[:8]
    output_path = output_dir / f"{image_path.stem}_{path_hash}.pdf"

    img = Image.open(image_path)

    # Handle multi-frame TIFF
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
