"""Migrate JSON sidecar files: rename 'hash' → 'content_hash' and '_old_hash' → 'file_hash'."""

import json
import sys
from pathlib import Path

from tqdm import tqdm


def migrate_json_files(directory: Path) -> tuple[int, int, int]:
    """Rename legacy keys in all JSON sidecar files.

    Returns:
        Tuple of (total_files, migrated_count, skipped_count)
    """
    json_files = list(directory.rglob("*.json"))
    migrated = 0
    skipped = 0

    for json_path in tqdm(json_files, desc="Migrating JSON keys"):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  SKIP (read error): {json_path.name}: {e}")
            skipped += 1
            continue

        changed = False

        # Rename "hash" → "content_hash" (only if "content_hash" doesn't already exist)
        if "hash" in data and "content_hash" not in data:
            data["content_hash"] = data.pop("hash")
            changed = True
        elif "hash" in data and "content_hash" in data:
            # Both exist — remove the old one
            del data["hash"]
            changed = True

        # Rename "_old_hash" → "file_hash" (only if "file_hash" doesn't already exist)
        if "_old_hash" in data and "file_hash" not in data:
            data["file_hash"] = data.pop("_old_hash")
            changed = True
        elif "_old_hash" in data and "file_hash" in data:
            # Both exist — remove the old one
            del data["_old_hash"]
            changed = True

        if changed:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            migrated += 1

    return len(json_files), migrated, skipped


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/migrate_json_keys.py <processed_directory>")
        sys.exit(1)

    directory = Path(sys.argv[1])
    if not directory.is_dir():
        print(f"Error: {directory} is not a directory")
        sys.exit(1)

    total, migrated, skipped = migrate_json_files(directory)
    print(f"\nDone: {total} files scanned, {migrated} migrated, {skipped} skipped")
