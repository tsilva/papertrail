#!/usr/bin/env python3
"""
Migrate duplicate PDFs to a duplicates folder based on image content.
Keeps one original per duplicate group and moves the rest.
"""

import os
import re
import shutil
from pathlib import Path
from collections import defaultdict
import argparse

from documentor.hashing import hash_file_content


def get_file_date(filename: str) -> str:
    """
    Extract the date from the filename (format: YYYY-MM-DD at the start).
    Returns empty string if no valid date found.
    """
    try:
        parts = filename.split(" - ")
        if len(parts) > 0:
            date_str = parts[0]
            # Basic validation: should be 10 characters and in format YYYY-MM-DD
            if len(date_str) == 10 and date_str.count("-") == 2:
                return date_str
    except:
        pass
    return ""


def migrate_duplicates(source_folder: str, dry_run: bool = True):
    """
    Identify and migrate duplicate PDFs to a duplicates folder.

    Args:
        source_folder: Path to the folder containing PDFs
        dry_run: If True, only simulate the migration without moving files
    """
    print(f"{'[DRY RUN] ' if dry_run else ''}Scanning folder: {source_folder}\n")

    # Create duplicates folder path
    duplicates_folder = os.path.join(source_folder, "duplicates")

    # Dictionary to map content digests to list of file paths
    digest_to_files = defaultdict(list)

    # Get all PDF files
    pdf_files = list(Path(source_folder).glob("*.pdf"))
    total_files = len(pdf_files)

    print(f"Found {total_files} PDF files in the root of processed folder\n")
    print("Rendering PDF pages and computing content digests...")
    print("-" * 80)

    for idx, pdf_file in enumerate(pdf_files, 1):
        print(f"[{idx}/{total_files}] Processing: {pdf_file.name}")

        digest = hash_file_content(pdf_file)

        if digest:
            digest_to_files[digest].append(str(pdf_file))
        else:
            print(f"  -> Failed to extract digest, skipping")

    print("\n" + "=" * 80)
    print("DUPLICATE MIGRATION")
    print("=" * 80 + "\n")

    # Find duplicates (groups with more than one file)
    duplicate_groups = {digest: files for digest, files in digest_to_files.items() if len(files) > 1}

    if not duplicate_groups:
        print("No duplicates found. Nothing to migrate.")
        return

    print(f"Found {len(duplicate_groups)} groups of duplicates\n")

    # Create duplicates folder if not in dry run mode
    if not dry_run and not os.path.exists(duplicates_folder):
        os.makedirs(duplicates_folder)
        print(f"Created duplicates folder: {duplicates_folder}\n")

    total_to_migrate = 0
    total_kept = 0

    for group_num, (digest, files) in enumerate(duplicate_groups.items(), 1):
        # Sort files by date (earliest first), then by filename
        files_with_dates = []
        for file_path in files:
            filename = os.path.basename(file_path)
            date = get_file_date(filename)
            files_with_dates.append((date, filename, file_path))

        # Sort by date (earliest first), then by filename
        files_with_dates.sort(key=lambda x: (x[0], x[1]))
        sorted_files = [f[2] for f in files_with_dates]

        # Keep the first file (earliest date), migrate the rest
        original_file = sorted_files[0]
        duplicates_to_migrate = sorted_files[1:]

        print(f"Group {group_num} - {len(files)} files (keeping 1, migrating {len(duplicates_to_migrate)}):")
        print(f"  [KEEP] {os.path.basename(original_file)}")

        total_kept += 1

        for dup_file in duplicates_to_migrate:
            filename = os.path.basename(dup_file)
            json_file = dup_file.replace(".pdf", ".json")

            dest_pdf = os.path.join(duplicates_folder, filename)
            dest_json = dest_pdf.replace(".pdf", ".json")

            # Extract hash from original and duplicate for naming
            original_hash = re.search(r'- ([a-f0-9]{8})\.pdf$', original_file)
            original_hash_id = original_hash.group(1) if original_hash else "unknown"

            # Create original copy name: duplicate_base_name - original <hash>.pdf
            dup_base = filename.replace(".pdf", "")
            original_copy_name = f"{dup_base} - original {original_hash_id}.pdf"
            original_copy_dest = os.path.join(duplicates_folder, original_copy_name)
            original_copy_json = original_copy_dest.replace(".pdf", ".json")

            print(f"  [MOVE] {filename}")

            if not dry_run:
                # Move duplicate PDF
                try:
                    shutil.move(dup_file, dest_pdf)
                    total_to_migrate += 1

                    # Move duplicate JSON if it exists
                    if os.path.exists(json_file):
                        shutil.move(json_file, dest_json)
                        print(f"         + {filename.replace('.pdf', '.json')}")

                    # Copy original PDF alongside for comparison
                    shutil.copy2(original_file, original_copy_dest)
                    print(f"         [COPY ORIGINAL] {original_copy_name}")

                    # Copy original JSON if it exists
                    original_json = original_file.replace(".pdf", ".json")
                    if os.path.exists(original_json):
                        shutil.copy2(original_json, original_copy_json)
                        print(f"         + {original_copy_name.replace('.pdf', '.json')}")

                except Exception as e:
                    print(f"         ERROR: {e}")
            else:
                total_to_migrate += 1
                # Check if JSON exists
                if os.path.exists(json_file):
                    print(f"         + {filename.replace('.pdf', '.json')}")
                print(f"         [COPY ORIGINAL] {original_copy_name}")
                original_json = original_file.replace(".pdf", ".json")
                if os.path.exists(original_json):
                    print(f"         + {original_copy_name.replace('.pdf', '.json')}")

        print()

    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total PDFs scanned: {total_files}")
    print(f"Duplicate groups found: {len(duplicate_groups)}")
    print(f"Files kept as originals: {total_kept}")
    print(f"Files {'to be ' if dry_run else ''}migrated to duplicates folder: {total_to_migrate}")
    print(f"Original copies {'to be ' if dry_run else ''}placed alongside duplicates: {total_to_migrate}")

    if dry_run:
        print("\n[DRY RUN] No files were actually moved.")
        print("Run with --execute to perform the migration.")
    else:
        print(f"\nMigration complete! Duplicates moved to: {duplicates_folder}")
        print(f"Each duplicate has its original copy alongside for manual verification.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate duplicate PDFs based on image content")
    parser.add_argument(
        "--folder",
        type=str,
        required=True,
        help="Path to the processed folder"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the migration (default is dry-run mode)"
    )

    args = parser.parse_args()

    if not os.path.exists(args.folder):
        print(f"Error: Folder not found: {args.folder}")
        exit(1)

    migrate_duplicates(args.folder, dry_run=not args.execute)
