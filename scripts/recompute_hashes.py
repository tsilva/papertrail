#!/usr/bin/env python3
"""Recompute and update hashes for all PDFs in the processed directory.

Use this after external operations that modify PDF files (e.g., compression)
to update the metadata with new hash values.
"""

import sys
from pathlib import Path
from datetime import datetime

from tqdm import tqdm

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from papertrail.hashing import hash_file_fast, hash_file_content
from papertrail.metadata import load_metadata_file, save_metadata_json


def recompute_hashes(processed_path: Path, dry_run: bool = False):
    """Recompute hashes for all PDFs and update metadata."""

    # Find all PDF files
    pdf_files = list(processed_path.glob("*.pdf"))
    print(f"Found {len(pdf_files)} PDF files")

    updated = 0
    unchanged = 0
    errors = []

    for pdf_path in tqdm(pdf_files, desc="Recomputing hashes"):
        json_path = pdf_path.with_suffix(".json")

        if not json_path.exists():
            errors.append(f"Missing metadata: {pdf_path.name}")
            continue

        try:
            # Load existing metadata
            metadata = load_metadata_file(json_path)

            # Compute new hashes
            new_file_hash = hash_file_fast(pdf_path)
            new_content_hash = hash_file_content(pdf_path)

            # Check if changed
            old_content_hash = metadata.content_hash
            if old_content_hash == new_content_hash:
                unchanged += 1
                continue  # No change needed

            if dry_run:
                print(f"\nWould update: {pdf_path.name}")
                print(f"  content_hash: {old_content_hash} → {new_content_hash}")
                print(f"  file_hash: {metadata.file_hash} → {new_file_hash}")
                updated += 1
                continue

            # Update metadata
            metadata.content_hash = new_content_hash
            metadata.file_hash = new_file_hash
            metadata.update_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Save
            save_metadata_json(pdf_path, metadata)
            updated += 1

        except Exception as e:
            errors.append(f"Error processing {pdf_path.name}: {e}")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"{'[DRY RUN] ' if dry_run else ''}Summary:")
    print(f"  Updated: {updated} files")
    print(f"  Unchanged: {unchanged} files")
    print(f"  Errors: {len(errors)}")

    if errors:
        print(f"\nFirst {min(10, len(errors))} errors:")
        for err in errors[:10]:
            print(f"  - {err}")

    if dry_run:
        print(f"\n[DRY RUN] No files were modified. Run without --dry-run to apply changes.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Recompute hashes for all PDFs")
    parser.add_argument("processed_path", type=Path, help="Path to processed directory")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without saving")
    args = parser.parse_args()

    if not args.processed_path.exists():
        print(f"Error: Directory not found: {args.processed_path}")
        sys.exit(1)

    recompute_hashes(args.processed_path, dry_run=args.dry_run)
