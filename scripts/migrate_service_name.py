#!/usr/bin/env python3
"""Migrate service_name field into document_title.

Usage:
    python scripts/migrate_service_name.py <directory> [--dry-run]

For each JSON sidecar file:
- If service_name is populated: set document_title = service_name
  (old document_title was verbatim heading, now redefined as subject/product/service)
- If service_name is null: set document_title = null
  (old verbatim heading is redundant with document_type_raw)
- Remove the service_name field
- Update date_updated

After running, use `rename_files` to update filenames.
"""

import json
import sys
from datetime import datetime
from pathlib import Path


def migrate_file(json_path: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Migrate a single JSON file. Returns (changed, message)."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    service_name = data.get("service_name")
    has_service_name = "service_name" in data

    if not has_service_name:
        return False, "no service_name field"

    old_title = data.get("document_title")
    parts = []

    if service_name:
        if old_title:
            parts.append(f"document_title: '{old_title}' → '{service_name}'")
        else:
            parts.append(f"document_title: null → '{service_name}'")
        if not dry_run:
            data["document_title"] = service_name
    else:
        if old_title:
            parts.append(f"document_title: '{old_title}' → null (was verbatim heading)")
        else:
            parts.append(f"document_title: null (unchanged)")
        if not dry_run:
            data["document_title"] = None

    if not dry_run:
        del data["service_name"]
        data["date_updated"] = datetime.now().strftime("%Y-%m-%d")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False, sort_keys=True)

    return True, "; ".join(parts)


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
