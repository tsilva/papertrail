"""Merge exported PDFs into aggregate documents."""

from __future__ import annotations

import re
from pathlib import Path

import fitz

from papertrail.logging_utils import get_logger

logger = get_logger("pdf_merge")

_PREFIX_RE = re.compile(r"^([A-Za-z0-9]+)_")


def _iter_source_pdfs(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.glob("*.pdf")
        if not path.name.startswith("merged_")
    )


def _merge_pdfs(output_path: Path, pdf_paths: list[Path]) -> None:
    merged = fitz.open()
    try:
        for pdf_path in pdf_paths:
            with fitz.open(pdf_path) as src:
                merged.insert_pdf(src)
        merged.save(output_path)
    finally:
        merged.close()


def merge_all_pdfs(directory: str | Path) -> dict[str, Path]:
    directory_path = Path(directory)
    source_pdfs = _iter_source_pdfs(directory_path)
    if not source_pdfs:
        return {}

    merged_paths: dict[str, Path] = {}

    all_output = directory_path / "merged_all.pdf"
    _merge_pdfs(all_output, source_pdfs)
    merged_paths["all"] = all_output

    groups: dict[str, list[Path]] = {}
    for pdf_path in source_pdfs:
        match = _PREFIX_RE.match(pdf_path.name)
        if not match:
            continue
        prefix = match.group(1).lower()
        groups.setdefault(prefix, []).append(pdf_path)

    for prefix, pdf_paths in sorted(groups.items()):
        output_path = directory_path / f"merged_{prefix}.pdf"
        _merge_pdfs(output_path, pdf_paths)
        merged_paths[prefix] = output_path

    logger.debug(f"[PDF-MERGE] {directory_path}: created {len(merged_paths)} merged file(s)")
    return merged_paths
