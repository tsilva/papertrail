#!/usr/bin/env python3
"""Truncate overly long document_title values in metadata JSON files.

Usage:
    python scripts/migrate_truncate_titles.py <directory> [--dry-run]

For each JSON sidecar file:
- If document_title exceeds 80 characters, truncate at word boundary
- Update date_updated

After running, use `rename_files` to update filenames.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

MAX_TITLE_LENGTH = 80


def truncate_at_word_boundary(text: str, max_len: int) -> str:
    """Truncate text to max_len at a word boundary."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated


def migrate_file(json_path: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Migrate a single JSON file. Returns (changed, message)."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    title = data.get("document_title")
    if not title or len(title) <= MAX_TITLE_LENGTH:
        return False, "title OK or null"

    new_title = truncate_at_word_boundary(title, MAX_TITLE_LENGTH)
    message = f"document_title: '{title}' → '{new_title}' ({len(title)} → {len(new_title)} chars)"

    if not dry_run:
        data["document_title"] = new_title
        data["date_updated"] = datetime.now().strftime("%Y-%m-%d")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False, sort_keys=True)

    return True, message


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
