#!/usr/bin/env python3
"""
Proof of concept: Detect duplicate PDFs based on image content rather than file hash.
This extracts all images from PDFs and creates a digest based on image content.
"""

import hashlib
import os
from pathlib import Path
from collections import defaultdict
import fitz  # PyMuPDF


def extract_images_digest(pdf_path: str) -> str | None:
    """
    Extract all images from a PDF and create a digest based on their content.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        A SHA256 digest of all image content, or None if extraction fails
    """
    try:
        doc = fitz.open(pdf_path)
        image_hashes = []

        # Iterate through all pages
        for page_num in range(len(doc)):
            page = doc[page_num]
            image_list = page.get_images(full=True)

            # Extract each image
            for img_index, img in enumerate(image_list):
                xref = img[0]  # Get the XREF of the image

                try:
                    # Extract the image
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]

                    # Hash the image bytes
                    img_hash = hashlib.sha256(image_bytes).hexdigest()
                    image_hashes.append(img_hash)

                except Exception as e:
                    print(f"  Warning: Could not extract image {img_index} from page {page_num}: {e}")
                    continue

        doc.close()

        # Sort hashes to ensure consistent ordering
        image_hashes.sort()

        # Create a digest of all image hashes combined
        if image_hashes:
            combined = "".join(image_hashes)
            content_digest = hashlib.sha256(combined.encode()).hexdigest()
            return content_digest
        else:
            # No images found - return empty digest
            return "NO_IMAGES"

    except Exception as e:
        print(f"Error processing {pdf_path}: {e}")
        return None


def find_duplicates(folder_path: str):
    """
    Scan a folder for duplicate PDFs based on image content.

    Args:
        folder_path: Path to the folder containing PDFs
    """
    print(f"Scanning folder: {folder_path}\n")

    # Dictionary to map content digests to list of file paths
    digest_to_files = defaultdict(list)

    # Get all PDF files
    pdf_files = list(Path(folder_path).rglob("*.pdf"))
    total_files = len(pdf_files)

    print(f"Found {total_files} PDF files\n")
    print("Extracting image content from PDFs...")
    print("-" * 80)

    for idx, pdf_file in enumerate(pdf_files, 1):
        print(f"[{idx}/{total_files}] Processing: {pdf_file.name}")

        digest = extract_images_digest(str(pdf_file))

        if digest:
            digest_to_files[digest].append(str(pdf_file))
            print(f"  → Digest: {digest[:16]}...")
        else:
            print(f"  → Failed to extract digest")

    print("\n" + "=" * 80)
    print("DUPLICATE DETECTION RESULTS")
    print("=" * 80 + "\n")

    # Find duplicates (groups with more than one file)
    duplicates_found = False
    duplicate_groups = {digest: files for digest, files in digest_to_files.items() if len(files) > 1}

    if duplicate_groups:
        print(f"Found {len(duplicate_groups)} groups of duplicates:\n")

        for group_num, (digest, files) in enumerate(duplicate_groups.items(), 1):
            duplicates_found = True
            print(f"Group {group_num} (Digest: {digest[:16]}...) - {len(files)} files:")
            for file_path in files:
                file_size = os.path.getsize(file_path)
                print(f"  - {Path(file_path).name}")
                print(f"    Path: {file_path}")
                print(f"    Size: {file_size:,} bytes")
            print()
    else:
        print("No duplicates found based on image content.")
        print("\nThis suggests that file hash differences may be due to:")
        print("  - Different metadata")
        print("  - Different creation dates")
        print("  - Different PDF versions or compression")
        print("  - Different embedded fonts or other non-image content")

    # Summary statistics
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total PDFs scanned: {total_files}")
    print(f"Unique content signatures: {len(digest_to_files)}")
    print(f"Duplicate groups found: {len(duplicate_groups)}")
    if duplicate_groups:
        total_duplicates = sum(len(files) - 1 for files in duplicate_groups.values())
        print(f"Total duplicate files: {total_duplicates}")


if __name__ == "__main__":
    # Folder path from .env
    folder = "/Users/tsilva/Google Drive/My Drive/documentor-puzzle/processed/"

    if not os.path.exists(folder):
        print(f"Error: Folder not found: {folder}")
        exit(1)

    find_duplicates(folder)
