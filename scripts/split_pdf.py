#!/usr/bin/env python3
"""
Split a multi-page PDF into individual single-page PDFs.

Extracts document type and number from each page's text to generate
meaningful filenames. Falls back to page_N.pdf for unparseable pages.
"""

import argparse
import re
import sys
from pathlib import Path

import fitz


def extract_doc_info(text: str) -> tuple[str, str] | None:
    """Extract document type and number from page text.

    Returns (doc_type_slug, doc_number_slug) or None if unparseable.
    """
    # Fatura-Recibo: look for "N.º FR IDSM126/01039471"
    m = re.search(r"N\.º\s+(FR\s+\S+)", text)
    if m:
        number = m.group(1).replace("/", "-").replace(" ", "-")
        return "fatura-recibo", number

    # Nota de Lancamento: look for "Nr.Doc. 000013872301834"
    m = re.search(r"Nr\.Doc\.\s+(\d+)", text)
    if m:
        return "nota-lancamento", m.group(1)

    return None


def split_pdf(input_path: Path, output_dir: Path) -> list[Path]:
    """Split a PDF into individual single-page PDFs.

    Returns list of created file paths.
    """
    doc = fitz.open(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    seen: dict[str, int] = {}
    created: list[Path] = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()

        info = extract_doc_info(text)
        if info:
            doc_type, doc_number = info
            base_name = f"{doc_type}_{doc_number}"
        else:
            base_name = f"page_{page_num + 1}"

        # Handle duplicates
        if base_name in seen:
            seen[base_name] += 1
            filename = f"{base_name}_{seen[base_name]}.pdf"
        else:
            seen[base_name] = 1
            filename = f"{base_name}.pdf"

        out_path = output_dir / filename
        new_doc = fitz.open()
        new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
        new_doc.save(out_path)
        new_doc.close()

        created.append(out_path)
        print(f"  Page {page_num + 1} -> {filename}")

    doc.close()
    return created


def main():
    parser = argparse.ArgumentParser(
        description="Split a multi-page PDF into individual single-page PDFs."
    )
    parser.add_argument("input_pdf", type=str, help="Path to the input PDF file")
    parser.add_argument(
        "-o", "--output-dir", type=str, default=None,
        help="Output directory (default: same directory as input PDF)",
    )
    args = parser.parse_args()

    input_path = Path(args.input_pdf).expanduser().resolve()
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else input_path.parent

    print(f"Splitting: {input_path}")
    print(f"Output:    {output_dir}")
    print()

    created = split_pdf(input_path, output_dir)
    print(f"\nDone: {len(created)} files created")


if __name__ == "__main__":
    main()
