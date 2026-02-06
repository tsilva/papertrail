"""Migrate JSON sidecar files: rename metadata fields for sorted grouping.

Renames:
  confidence    -> class_confidence
  reasoning     -> class_reasoning
  create_date   -> date_created
  issue_date    -> date_issued
  update_date   -> date_updated
  content_hash  -> hash_content
  file_hash     -> hash_file

Also cleans up stale legacy key 'hash' -> 'hash_content'.
"""

import json
import sys
from pathlib import Path

from tqdm import tqdm

FIELD_RENAME_MAP = {
    "confidence": "class_confidence",
    "reasoning": "class_reasoning",
    "create_date": "date_created",
    "issue_date": "date_issued",
    "update_date": "date_updated",
    "content_hash": "hash_content",
    "file_hash": "hash_file",
    # Legacy key from earlier migration
    "hash": "hash_content",
}


def migrate_json_files(directory: Path, dry_run: bool = False) -> tuple[int, int, int]:
    """Rename metadata field names in all JSON sidecar files.

    Returns:
        Tuple of (total_files, migrated_count, skipped_count)
    """
    json_files = list(directory.rglob("*.json"))
    migrated = 0
    skipped = 0

    for json_path in tqdm(json_files, desc="Migrating field names"):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  SKIP (read error): {json_path.name}: {e}")
            skipped += 1
            continue

        changed = False

        for old_key, new_key in FIELD_RENAME_MAP.items():
            if old_key not in data:
                continue

            if new_key in data:
                # New key already exists — just remove the old one
                del data[old_key]
                changed = True
            else:
                # Rename: old -> new
                data[new_key] = data.pop(old_key)
                changed = True

        if changed:
            if dry_run:
                print(f"  WOULD MIGRATE: {json_path.name}")
            else:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4, ensure_ascii=False, sort_keys=True)
            migrated += 1

    return len(json_files), migrated, skipped


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/migrate_field_names.py <processed_directory> [--dry-run]")
        sys.exit(1)

    directory = Path(sys.argv[1])
    if not directory.is_dir():
        print(f"Error: {directory} is not a directory")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("DRY RUN - no files will be modified\n")

    total, migrated, skipped = migrate_json_files(directory, dry_run=dry_run)
    print(f"\nDone: {total} files scanned, {migrated} migrated, {skipped} skipped")
