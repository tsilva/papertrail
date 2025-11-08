#!/usr/bin/env python3
"""
Update all document metadata with new content-based hash (new_hash field).
This uses the same hashing scheme from migrate_duplicates.py - rendering pages as images.
"""

import hashlib
import json
import os
import sys
from pathlib import Path
from tqdm import tqdm
import fitz  # PyMuPDF


def extract_images_digest(pdf_path: str) -> str | None:
    """
    Render each PDF page as an image and create a digest based on the rendered content.
    This approach captures all visual content including text, graphics, and embedded images.

    This is the NEW hashing scheme that should be used going forward.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        First 8 characters of SHA256 digest of all rendered page images, or None if rendering fails
    """
    try:
        doc = fitz.open(pdf_path)
        page_hashes = []

        # Use deterministic rendering settings for consistency
        # 150 DPI provides good quality while being reasonably fast
        zoom = 150 / 72  # 72 is the default DPI
        mat = fitz.Matrix(zoom, zoom)

        # Iterate through all pages and render each as an image
        for page_num in range(len(doc)):
            page = doc[page_num]

            try:
                # Render page as pixmap (image) with deterministic settings
                # alpha=False ensures no transparency channel for consistency
                pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)

                # Get the raw pixel data
                img_data = pix.samples

                # Hash the pixel data
                page_hash = hashlib.sha256(img_data).hexdigest()
                page_hashes.append(page_hash)

            except Exception as e:
                print(f"  Warning: Could not render page {page_num}: {e}")
                continue

        doc.close()

        # Create a digest of all page hashes combined (in order)
        if page_hashes:
            combined = "".join(page_hashes)
            content_digest = hashlib.sha256(combined.encode()).hexdigest()
            # Return only first 8 characters to match old hash format
            return content_digest[:8]
        else:
            # No pages could be rendered
            return None

    except Exception as e:
        print(f"  Error processing {pdf_path}: {e}")
        return None


def update_document_hashes(processed_folder: str, dry_run: bool = True):
    """
    Update all document metadata files with the new content-based hash.

    Args:
        processed_folder: Path to the processed files directory
        dry_run: If True, only show what would be updated without actually updating
    """
    processed_path = Path(processed_folder)

    if not processed_path.exists():
        print(f"Error: Folder not found: {processed_folder}")
        return

    # Get all JSON metadata files
    json_files = list(processed_path.glob("*.json"))
    total_files = len(json_files)

    print(f"{'[DRY RUN] ' if dry_run else ''}Found {total_files} metadata files to process\n")

    success_count = 0
    error_count = 0
    skipped_count = 0

    for json_file in tqdm(json_files, desc="Processing documents"):
        pdf_file = json_file.with_suffix(".pdf")

        # Check if PDF exists
        if not pdf_file.exists():
            print(f"\n[SKIP] PDF not found for {json_file.name}")
            skipped_count += 1
            continue

        try:
            # Read existing metadata
            with open(json_file, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            # Check if new_hash already exists (optional - for re-running)
            if "new_hash" in metadata and not dry_run:
                # Skip if already has new_hash (unless we want to recalculate)
                # Comment this out if you want to recalculate existing new_hash values
                # skipped_count += 1
                # continue
                pass

            # Calculate new content-based hash
            new_hash = extract_images_digest(str(pdf_file))

            if new_hash is None:
                print(f"\n[ERROR] Failed to calculate hash for {pdf_file.name}")
                error_count += 1
                continue

            # Update metadata with new_hash field
            metadata["new_hash"] = new_hash

            if not dry_run:
                # Save updated metadata back to file
                with open(json_file, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=4)

            success_count += 1

        except Exception as e:
            print(f"\n[ERROR] Failed to process {json_file.name}: {e}")
            error_count += 1

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total metadata files: {total_files}")
    print(f"Successfully {'would be ' if dry_run else ''}updated: {success_count}")
    print(f"Errors: {error_count}")
    print(f"Skipped: {skipped_count}")

    if dry_run:
        print("\n[DRY RUN] No files were actually modified.")
        print("Run with --execute to perform the update.")
    else:
        print(f"\nUpdate complete! All metadata files now have 'new_hash' field.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Update all document metadata with new content-based hash"
    )
    parser.add_argument(
        "--folder",
        type=str,
        help="Path to the processed folder (uses PROCESSED_FILES_DIR from .env if not specified)"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the update (default is dry-run mode)"
    )

    args = parser.parse_args()

    # Get folder path from args or .env
    if args.folder:
        folder_path = args.folder
    else:
        # Load from .env
        from dotenv import load_dotenv
        config_dir = Path.home() / ".documentor"
        env_path = config_dir / ".env"

        if not env_path.exists():
            print(f"Error: .env file not found at {env_path}")
            print("Please specify --folder argument or ensure .env is configured")
            sys.exit(1)

        load_dotenv(dotenv_path=env_path, override=True)
        folder_path = os.getenv("PROCESSED_FILES_DIR")

        if not folder_path:
            print("Error: PROCESSED_FILES_DIR not set in .env")
            print("Please set PROCESSED_FILES_DIR in .env or use --folder argument")
            sys.exit(1)

    update_document_hashes(folder_path, dry_run=not args.execute)
