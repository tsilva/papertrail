"""Sort all keys in JSON sidecar files alphabetically."""

import json
import sys
from pathlib import Path

from tqdm import tqdm


def sort_json_keys(directory: Path) -> tuple[int, int, int]:
    """Re-write all JSON files with sorted keys.

    Returns:
        Tuple of (total_files, migrated_count, skipped_count)
    """
    json_files = list(directory.rglob("*.json"))
    migrated = 0
    skipped = 0

    for json_path in tqdm(json_files, desc="Sorting JSON keys"):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  SKIP (read error): {json_path.name}: {e}")
            skipped += 1
            continue

        # Check if keys are already sorted
        serialized_original = json.dumps(data, indent=4, ensure_ascii=False)
        serialized_sorted = json.dumps(data, indent=4, ensure_ascii=False, sort_keys=True)

        if serialized_original == serialized_sorted:
            continue

        with open(json_path, "w", encoding="utf-8") as f:
            f.write(serialized_sorted)
        migrated += 1

    return len(json_files), migrated, skipped


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/sort_json_keys.py <directory>")
        sys.exit(1)

    directory = Path(sys.argv[1])
    if not directory.is_dir():
        print(f"Error: {directory} is not a directory")
        sys.exit(1)

    total, migrated, skipped = sort_json_keys(directory)
    print(f"\nDone: {total} files scanned, {migrated} migrated, {skipped} skipped")
