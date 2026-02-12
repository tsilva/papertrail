#!/usr/bin/env python3
"""Add file_size_kb to sidecar JSON files by measuring companion document files.

Usage:
    python scripts/migrate_add_file_size.py <directory> [--dry-run]

For each JSON sidecar file:
- Find the companion document file (.pdf or .xlsx via source_extension)
- Compute file size in KB (rounded)
- Set file_size_kb and update date_updated

Skips files that already have file_size_kb or where the companion file is missing.
"""

import json
import sys
from datetime import datetime
from pathlib import Path


def _find_companion(json_path: Path, data: dict) -> Path | None:
    """Find the companion document file for a JSON sidecar."""
    ext = data.get("source_extension")
    if ext:
        candidate = json_path.with_suffix(ext)
        if candidate.exists():
            return candidate

    for fallback_ext in (".pdf", ".xlsx"):
        candidate = json_path.with_suffix(fallback_ext)
        if candidate.exists():
            return candidate

    return None


def migrate_file(json_path: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Migrate a single JSON file. Returns (changed, message)."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("file_size_kb") is not None:
        return False, "already has file_size_kb"

    companion = _find_companion(json_path, data)
    if companion is None:
        return False, "companion file not found"

    size_kb = round(companion.stat().st_size / 1024)

    if not dry_run:
        data["file_size_kb"] = size_kb
        data["date_updated"] = datetime.now().strftime("%Y-%m-%d")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False, sort_keys=True)

    return True, f"file_size_kb={size_kb} (from {companion.name})"


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
    migrated_count = 0
    skipped_count = 0

    for json_path in json_files:
        if "/logs/" in str(json_path):
            continue

        try:
            changed, message = migrate_file(json_path, dry_run)
            if changed:
                migrated_count += 1
                print(f"  [MIGRATED] {json_path.name}: {message}")
            else:
                skipped_count += 1
        except Exception as e:
            print(f"  [ERROR] {json_path.name}: {e}")

    print(f"\nSummary: {migrated_count} migrated, {skipped_count} skipped")


if __name__ == "__main__":
    main()
