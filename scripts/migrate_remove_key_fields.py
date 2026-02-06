#!/usr/bin/env python3
"""Remove document_type_key and issuing_party_key from sidecar JSON files.

Usage:
    python scripts/migrate_remove_key_fields.py <directory> [--dry-run]

For each JSON sidecar file:
- Remove document_type_key field (if present)
- Remove issuing_party_key field (if present)
- Update date_updated

After running, use `rename_files` to update filenames.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

FIELDS_TO_REMOVE = ("document_type_key", "issuing_party_key")


def migrate_file(json_path: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Migrate a single JSON file. Returns (changed, message)."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    removed = []
    for field in FIELDS_TO_REMOVE:
        if field in data:
            removed.append(f"{field}={data[field]!r}")
            if not dry_run:
                del data[field]

    if not removed:
        return False, "no key fields"

    if not dry_run:
        data["date_updated"] = datetime.now().strftime("%Y-%m-%d")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False, sort_keys=True)

    return True, "removed " + ", ".join(removed)


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <directory> [--dry-run]")
        sys.exit(1)

    directory = Path(sys.argv[1])
    dry_run = "--dry-run" in sys.argv

    if not directory.is_dir():
        print(f"Error: {directory} is not a directory")
        sys.exit(1)

    if dry_run:
        print("DRY RUN — no files will be modified\n")

    json_files = sorted(directory.rglob("*.json"))
    changed_count = 0
    skipped_count = 0

    for json_path in json_files:
        if "/logs/" in str(json_path):
            continue

        try:
            changed, message = migrate_file(json_path, dry_run)
            if changed:
                changed_count += 1
                print(f"  [MIGRATED] {json_path.name}: {message}")
            else:
                skipped_count += 1
        except Exception as e:
            print(f"  [ERROR] {json_path.name}: {e}")

    print(f"\nSummary: {changed_count} migrated, {skipped_count} skipped")


if __name__ == "__main__":
    main()
