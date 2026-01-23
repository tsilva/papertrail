#!/usr/bin/env python3
"""
Add file_hash (_old_hash) field to metadata files that are missing it.
"""

import json
import sys
from pathlib import Path

from tqdm import tqdm

from papertrail.config import load_config
from papertrail.hashing import hash_file_fast


def update_missing_file_hashes(processed_folder: str, dry_run: bool = True):
    """Add file_hash to metadata files that are missing it."""
    processed_path = Path(processed_folder)

    if not processed_path.exists():
        print(f"Error: Folder not found: {processed_folder}")
        return

    json_files = list(processed_path.glob("*.json"))
    total_files = len(json_files)

    print(f"{'[DRY RUN] ' if dry_run else ''}Scanning {total_files} metadata files...\n")

    files_needing_update = []

    # Find files missing _old_hash
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            if not metadata.get('_old_hash'):
                pdf_file = json_file.with_suffix(".pdf")
                if pdf_file.exists():
                    files_needing_update.append((json_file, pdf_file))
                else:
                    print(f"[SKIP] PDF not found for {json_file.name}")
        except Exception as e:
            print(f"[ERROR] Failed to read {json_file.name}: {e}")

    if not files_needing_update:
        print("All files already have file_hash (_old_hash) field!")
        return

    print(f"Found {len(files_needing_update)} files missing file_hash\n")

    success_count = 0
    error_count = 0

    for json_file, pdf_file in tqdm(files_needing_update, desc="Updating files"):
        try:
            # Read metadata
            with open(json_file, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            # Calculate file hash
            file_hash = hash_file_fast(pdf_file)

            # Add file hash
            metadata["_old_hash"] = file_hash

            if not dry_run:
                # Save updated metadata
                with open(json_file, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=4)

            success_count += 1
            print(f"\n{'[DRY RUN] Would add' if dry_run else 'Added'} _old_hash={file_hash} to {json_file.name}")

        except Exception as e:
            print(f"\n[ERROR] Failed to update {json_file.name}: {e}")
            error_count += 1

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total metadata files: {total_files}")
    print(f"Files needing update: {len(files_needing_update)}")
    print(f"Successfully {'would be ' if dry_run else ''}updated: {success_count}")
    print(f"Errors: {error_count}")

    if dry_run:
        print("\n[DRY RUN] No files were actually modified.")
        print("Run with --execute to perform the update.")
    else:
        print(f"\nUpdate complete!")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Add file_hash to metadata files that are missing it"
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

    update_missing_file_hashes(folder_path, dry_run=not args.execute)
