#!/usr/bin/env python3
"""
Update all document metadata with new content-based hash (new_hash field).
Uses the content-based hashing from papertrail package.
"""

import json
import os
import sys
from pathlib import Path

from tqdm import tqdm

from papertrail.config import load_config
from papertrail.hashing import hash_file_content


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

            # Calculate new content-based hash
            new_hash = hash_file_content(pdf_file)

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

    # Get folder path from args or config
    if args.folder:
        folder_path = args.folder
    else:
        config = load_config()
        folder_path = config.get("PROCESSED_FILES_DIR")

        if not folder_path:
            print("Error: PROCESSED_FILES_DIR not set in .env")
            print("Please set PROCESSED_FILES_DIR in .env or use --folder argument")
            sys.exit(1)

    update_document_hashes(folder_path, dry_run=not args.execute)
